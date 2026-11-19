"""Resume tailoring by SELECTION ONLY: choose and reorder true bullets from the bank.

Nothing here writes new text. A bullet is either copied verbatim from the bank
or left out; skill items are only reordered. `verify_truthful` re-checks that
invariant on every generated resume, and a hypothesis property test checks it
on thousands of random inputs.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from jobpulse.bank import Bank
from jobpulse.models import Extraction
from jobpulse.taxonomy import Taxonomy

MAX_PROJECTS = 3
MAX_BULLETS_PER_PROJECT = 3
MAX_SKILL_GROUPS = 5
REQUIRED_WEIGHT = 2.0
NICE_WEIGHT = 1.0

_TOKEN = re.compile(r"[a-z0-9+#.]+")


def tokenize(text: str) -> list[str]:
    return [t.strip(".") for t in _TOKEN.findall(text.lower()) if t.strip(".")]


class BM25:
    """Okapi BM25 over a small corpus (the bank's bullets)."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self.avgdl = sum(map(len, docs)) / len(docs) if docs else 0.0
        df: Counter[str] = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], i: int) -> float:
        doc = self.docs[i]
        tf = Counter(doc)
        norm = self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl) if self.avgdl else self.k1
        return sum(
            self.idf.get(q, 0.0) * tf[q] * (self.k1 + 1) / (tf[q] + norm) for q in set(query) if q in tf
        )


@dataclass(frozen=True)
class ChosenProject:
    key: str
    name: str
    desc: str
    bullets: tuple[str, ...]


@dataclass
class TailoredResume:
    skills: list[tuple[str, tuple[str, ...]]]  # (label, items in display order)
    projects: list[ChosenProject]
    experience: list[tuple[dict[str, object], tuple[str, ...]]]
    bullet_scores: dict[str, float] = field(default_factory=dict)

    def visible_text(self) -> str:
        parts = [f"{label}: {', '.join(items)}" for label, items in self.skills]
        for p in self.projects:
            parts += [p.name, p.desc, *p.bullets]
        for _, bullets in self.experience:
            parts += list(bullets)
        return "\n".join(parts)

    def all_bullets(self) -> list[str]:
        return [b for p in self.projects for b in p.bullets] + [b for _, bs in self.experience for b in bs]


def _query_tokens(extraction: Extraction, tax: Taxonomy) -> list[str]:
    tokens: list[str] = []
    for skill in extraction.all_skills:
        s = tax.skills.get(skill)
        names = [skill, *(s.aliases if s else ()), *(s.exact if s else ())]
        tokens += [t for n in names for t in tokenize(n)]
    return tokens


def score_bullets(bank: Bank, extraction: Extraction, tax: Taxonomy) -> dict[tuple[str, str], float]:
    """Score = BM25(bullet, requirement terms) + taxonomy match bonus (required x2, nice x1)."""
    keys = [(p.key, bk) for p in bank.projects.values() for bk in p.bullets]
    texts = [f"{bank.projects[pk].desc} {bank.projects[pk].bullets[bk]}" for pk, bk in keys]
    bm25 = BM25([tokenize(t) for t in texts])
    query = _query_tokens(extraction, tax)
    required, nice = set(extraction.required_skills), set(extraction.nice_to_have)
    scores = {}
    for i, key in enumerate(keys):
        found = tax.find(texts[i])
        bonus = REQUIRED_WEIGHT * len(found & required) + NICE_WEIGHT * len(found & nice)
        scores[key] = round(bm25.score(query, i) + bonus, 4)
    return scores


def _order_skills(bank: Bank, extraction: Extraction, tax: Taxonomy) -> list[tuple[str, tuple[str, ...]]]:
    wanted = extraction.all_skills

    def item_hits(item: str) -> int:
        return len(tax.find(item) & wanted)

    groups = []
    for order, g in enumerate(bank.skills.values()):
        items = sorted(g.items, key=lambda it: -item_hits(it))  # stable: matched items first
        relevance = sum(item_hits(it) for it in g.items)
        groups.append((relevance, -order, g.label, tuple(items)))
    groups.sort(reverse=True)
    return [(label, items) for _, _, label, items in groups[:MAX_SKILL_GROUPS]]


def tailor(bank: Bank, extraction: Extraction, tax: Taxonomy) -> TailoredResume:
    scores = score_bullets(bank, extraction, tax)
    ranked_projects = []
    for order, p in enumerate(bank.projects.values()):
        bullet_scores = sorted(
            ((scores[(p.key, bk)], -i, bk) for i, bk in enumerate(p.bullets)), reverse=True
        )
        project_score = sum(s for s, _, _ in bullet_scores[:2])
        ranked_projects.append((project_score, -order, p, bullet_scores))
    ranked_projects.sort(key=lambda t: (t[0], t[1]), reverse=True)

    chosen = []
    for _, _, p, bullet_scores in ranked_projects[:MAX_PROJECTS]:
        picked = [bk for s, _, bk in bullet_scores if s > 0][:MAX_BULLETS_PER_PROJECT] or [
            bullet_scores[0][2]
        ]
        chosen.append(ChosenProject(p.key, p.name, p.desc, tuple(p.bullets[bk] for bk in picked)))

    return TailoredResume(
        skills=_order_skills(bank, extraction, tax),
        projects=chosen,
        experience=[(e, tuple(e.get("bullets", []))) for e in bank.experience],
        bullet_scores={bank.projects[pk].bullets[bk]: s for (pk, bk), s in scores.items()},
    )


def default_resume(bank: Bank) -> TailoredResume:
    """The untailored resume (bank's [default] section): the 'before' in coverage comparisons."""
    spec = bank.default_spec
    skill_keys = spec.get("skills") or list(bank.skills)[:MAX_SKILL_GROUPS]
    project_specs = spec.get("projects") or [
        {"key": k, "bullets": list(p.bullets)[:2]} for k, p in list(bank.projects.items())[:MAX_PROJECTS]
    ]
    return TailoredResume(
        skills=[(bank.skills[k].label, bank.skills[k].items) for k in skill_keys],
        projects=[
            ChosenProject(
                ps["key"],
                bank.projects[ps["key"]].name,
                bank.projects[ps["key"]].desc,
                tuple(bank.projects[ps["key"]].bullets[b] for b in ps["bullets"]),
            )
            for ps in project_specs
        ],
        experience=[(e, tuple(e.get("bullets", []))) for e in bank.experience],
    )


def shrink(resume: TailoredResume) -> bool:
    """Drop the single lowest-scoring project bullet (keeping >= 1 per project), else the last project.

    Used by the overflow guard. Returns False when nothing more can be removed.
    """
    candidates = [
        (resume.bullet_scores.get(b, 0.0), pi, b)
        for pi, p in enumerate(resume.projects)
        if len(p.bullets) > 1
        for b in p.bullets
    ]
    if candidates:
        _, pi, victim = min(candidates, key=lambda t: (t[0], -t[1]))
        p = resume.projects[pi]
        resume.projects[pi] = ChosenProject(p.key, p.name, p.desc, tuple(b for b in p.bullets if b != victim))
        return True
    if len(resume.projects) > 1:
        resume.projects.pop()
        return True
    return False


_NUMBER = re.compile(r"\d[\d,.]*%?")


class TruthfulnessError(AssertionError):
    pass


def verify_truthful(resume: TailoredResume, bank: Bank) -> None:
    """Raise TruthfulnessError unless every claim on the resume is copied verbatim from the bank."""
    bank_bullets = bank.all_bullets()
    bank_numbers = Counter(n for b in bank_bullets for n in _NUMBER.findall(b))
    for bullet in resume.all_bullets():
        if bullet not in bank_bullets:
            raise TruthfulnessError(f"bullet not in bank: {bullet[:80]}")
        for n, count in Counter(_NUMBER.findall(bullet)).items():
            if bank_numbers[n] < count:
                raise TruthfulnessError(f"number {n} not in bank")
    groups = {g.label: g.items for g in bank.skills.values()}
    for label, items in resume.skills:
        if label not in groups or sorted(items) != sorted(groups[label]):
            raise TruthfulnessError(f"skill line '{label}' is not a reordering of the bank's items")
    for p in resume.projects:
        bp = bank.projects.get(p.key)
        if bp is None or (p.name, p.desc) != (bp.name, bp.desc):
            raise TruthfulnessError(f"project header changed: {p.name}")


def coverage(resume: TailoredResume, extraction: Extraction, tax: Taxonomy) -> float:
    """Fraction of the JD's extracted skills that appear anywhere on the resume."""
    wanted = extraction.all_skills
    if not wanted:
        return 0.0
    return len(tax.find(resume.visible_text()) & wanted) / len(wanted)
