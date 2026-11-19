"""One-page PDF rendering with an overflow guard.

Layout adapted from `builder/build_resume.py` in jd-resume-tailor
(https://github.com/xungirl/jd-resume-tailor, MIT License, (c) 2026 xungirl).
Changes: renders a TailoredResume into memory (bytes) instead of a variant
file, and reports whether the content fits on one page so the caller can shrink it.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from jobpulse.bank import Bank
from jobpulse.pipeline.tailor import TailoredResume, shrink

WIDTH, HEIGHT = 612, 792  # US Letter, points
LEFT, RIGHT = 40, 572
TEXT_WIDTH = RIGHT - LEFT
BODY = 10.2
LEADING = BODY + 1.8
BOTTOM = 28
BLACK = HexColor("#151515")
LINK = "#183A70"
FONT = {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}
pdfmetrics.registerFontFamily(
    "Helvetica",
    normal="Helvetica",
    bold="Helvetica-Bold",
    italic="Helvetica-Oblique",
    boldItalic="Helvetica-BoldOblique",
)

_STYLES = {
    "body": ParagraphStyle("body", fontName=FONT["regular"], fontSize=BODY, leading=LEADING, textColor=BLACK),
    "contact": ParagraphStyle(
        "contact", fontName=FONT["regular"], fontSize=9.6, leading=12, alignment=1, textColor=BLACK
    ),
    "title": ParagraphStyle(
        "title", fontName=FONT["bold"], fontSize=22, leading=24, alignment=1, textColor=BLACK
    ),
}


def _markup(text: str) -> str:
    """Escape bare '&' for reportlab's mini-markup while keeping tags like <b> from the bank."""
    return re.sub(r"&(?!\w+;|#\d+;)", "&amp;", text)


@dataclass(frozen=True)
class RenderResult:
    pdf: bytes
    fits: bool
    bottom_y: float


def render(bank: Bank, resume: TailoredResume, title: str = "Resume") -> RenderResult:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    name = str(bank.profile.get("name", ""))
    c.setTitle(f"{name} - {title}")
    c.setAuthor(name)
    y = 757.0

    def para(t: str, style: str = "body", x: float = LEFT, width: float = TEXT_WIDTH, gap: float = 0) -> None:
        nonlocal y
        p = Paragraph(_markup(t), _STYLES[style])
        _, h = p.wrap(width, HEIGHT)
        p.drawOn(c, x, y - h)
        y -= h + gap

    def row(left: str, right: str = "", italic: bool = False, gap: float = 0) -> None:
        nonlocal y
        font = FONT["italic"] if italic else FONT["regular"]
        rw = pdfmetrics.stringWidth(right, font, BODY)
        p = Paragraph(_markup(left), _STYLES["body"])
        _, h = p.wrap(TEXT_WIDTH - rw - (14 if right else 0), HEIGHT)
        p.drawOn(c, LEFT, y - h)
        if right:
            c.setFont(font, BODY)
            c.setFillColor(BLACK)
            c.drawRightString(RIGHT, y - BODY, right)
        y -= max(h, LEADING) + gap

    def section(label: str) -> None:
        nonlocal y
        y -= 6
        c.setFont(FONT["bold"], 11.2)
        c.setFillColor(BLACK)
        c.drawString(LEFT, y - 10.5, label)
        c.setLineWidth(0.55)
        c.line(LEFT, y - 14, RIGHT, y - 14)
        y -= 18

    def bullet(t: str) -> None:
        top = y
        para(t, x=LEFT + 12, width=TEXT_WIDTH - 12, gap=1.0)
        c.setFillColor(BLACK)
        c.circle(LEFT + 4.2, top - 6.3, 1.3, fill=1, stroke=0)

    pr: dict[str, Any] = bank.profile
    para(name, "title", gap=3)
    links = [f'<link href="{x["url"]}" color="{LINK}">{x["label"]}</link>' for x in pr.get("links", [])]
    para(" | ".join([str(pr.get("location", "")), *links]), "contact", gap=1)

    section("EDUCATION")
    for i, e in enumerate(bank.education):
        if i:
            y -= 3
        row(f"<b>{e['school']}</b>", str(e.get("location", "")))
        row(f"<i>{e['degree']}</i>", str(e.get("dates", "")), italic=True)
        for line in e.get("lines", []):
            para(line)

    section("TECHNICAL SKILLS")
    for label, items in resume.skills:
        para(f"<b>{label}:</b> {', '.join(items)}")

    section("PROJECTS")
    for p in resume.projects:
        y -= 2.5
        row(f"<b>{p.name}</b> | <i>{p.desc}</i>", gap=1)
        for b in p.bullets:
            bullet(b)

    if resume.experience:
        section("EXPERIENCE")
        for e, bullets in resume.experience:
            row(f"<b>{e['company']}</b>", str(e.get("location", "")))
            row(f"<i>{e['title']}</i>", str(e.get("dates", "")), italic=True, gap=1)
            for b in bullets:
                bullet(b)

    c.showPage()
    c.save()
    return RenderResult(buf.getvalue(), y >= BOTTOM, round(y, 1))


def render_one_page(bank: Bank, resume: TailoredResume, title: str = "Resume") -> tuple[RenderResult, int]:
    """Render; while it overflows, drop the weakest bullet and retry. Returns (result, bullets dropped)."""
    dropped = 0
    result = render(bank, resume, title)
    while not result.fits and shrink(resume):
        dropped += 1
        result = render(bank, resume, title)
    return result, dropped
