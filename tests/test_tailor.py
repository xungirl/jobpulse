from __future__ import annotations

import pytest

from jobpulse.bank import parse_bank, split_items
from jobpulse.context import demo_bank_toml
from jobpulse.models import Extraction
from jobpulse.pipeline.render import render, render_one_page
from jobpulse.pipeline.tailor import (
    BM25,
    ChosenProject,
    TruthfulnessError,
    coverage,
    default_resume,
    tailor,
    tokenize,
    verify_truthful,
)
from jobpulse.taxonomy import Taxonomy


def ex(required: tuple[str, ...], nice: tuple[str, ...] = ()) -> Extraction:
    return Extraction(required, nice, "", (), (), method="test")


def test_bm25_prefers_matching_doc() -> None:
    bm = BM25([tokenize("raft key value store in go"), tokenize("react single page app")])
    q = tokenize("react typescript")
    assert bm.score(q, 1) > bm.score(q, 0) == 0


def test_tailor_picks_relevant_projects(tax: Taxonomy) -> None:
    bank = parse_bank(demo_bank_toml())
    ml = tailor(bank, ex(("PyTorch", "Computer Vision", "RAG")), tax)
    assert {"DocQA", "ShelfScan"} <= {p.name for p in ml.projects}
    backend = tailor(bank, ex(("Java", "Spring Boot", "Kafka", "PostgreSQL")), tax)
    assert backend.projects[0].name == "Shopline"
    assert backend.skills[0][0] == "Backend"  # most relevant skill line first


def test_skill_items_reordered_not_changed(tax: Taxonomy) -> None:
    bank = parse_bank(demo_bank_toml())
    r = tailor(bank, ex(("Kafka", "Redis")), tax)
    backend = dict(r.skills)["Backend"]
    assert backend[:2] == ("Redis", "Kafka") or backend[:2] == ("Kafka", "Redis")
    assert sorted(backend) == sorted(bank.skills["backend"].items)


def test_tailoring_improves_coverage_for_backend_jd(tax: Taxonomy) -> None:
    bank = parse_bank(demo_bank_toml())
    e = ex(("Kafka", "Terraform", "Serverless", "React"))
    assert coverage(tailor(bank, e, tax), e, tax) >= coverage(default_resume(bank), e, tax)


def test_verify_truthful_catches_edits(tax: Taxonomy) -> None:
    bank = parse_bank(demo_bank_toml())
    r = tailor(bank, ex(("Python",)), tax)
    verify_truthful(r, bank)
    p = r.projects[0]
    r.projects[0] = ChosenProject(p.key, p.name, p.desc, (p.bullets[0] + " Led a team of 50.",))
    with pytest.raises(TruthfulnessError):
        verify_truthful(r, bank)


def test_verify_truthful_catches_added_skill(tax: Taxonomy) -> None:
    bank = parse_bank(demo_bank_toml())
    r = tailor(bank, ex(("Python",)), tax)
    label, items = r.skills[0]
    r.skills[0] = (label, (*items, "Haskell"))
    with pytest.raises(TruthfulnessError):
        verify_truthful(r, bank)


def test_render_fits_one_page_and_overflow_guard(tax: Taxonomy) -> None:
    bank = parse_bank(demo_bank_toml())
    r = tailor(bank, ex(("Python", "Go", "Kafka", "PyTorch", "AWS")), tax)
    result, _ = render_one_page(bank, r)
    assert result.fits and result.pdf.startswith(b"%PDF")
    # Force overflow by cramming every project and bullet, then let the guard shrink it.
    r.projects = [
        ChosenProject(p.key, p.name, p.desc, tuple(p.bullets.values())) for p in bank.projects.values()
    ]
    r.experience = r.experience * 4
    assert not render(bank, r).fits
    result, dropped = render_one_page(bank, r)
    assert result.fits and dropped > 0
    verify_truthful(r, bank)


def test_split_items_respects_parentheses() -> None:
    assert split_items("AWS (ECS, S3), Docker; SQL") == ("AWS (ECS, S3)", "Docker", "SQL")


def test_jd_resume_tailor_bank_format_is_supported(tax: Taxonomy) -> None:
    toml = """
[profile]
name = "X"
[skills.a]
label = "Languages"
text = "Go, Python"
[projects.p]
name = "P"
desc = "d"
[projects.p.bullets]
one = "Built <b>3</b> things in Go."
[variants.v]
skills = ["a"]
projects = [{ key = "p", bullets = ["one"] }]
"""
    bank = parse_bank(toml)
    assert bank.skills["a"].items == ("Go", "Python")
    assert default_resume(bank).projects[0].bullets == ("Built <b>3</b> things in Go.",)
    verify_truthful(tailor(bank, ex(("Go",)), tax), bank)
