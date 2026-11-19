"""Property test of the core guarantee: tailoring never invents or edits a claim.

For random JD requirement sets AND random bullet banks, every bullet on every
generated resume (after the one-page overflow guard) must exist verbatim in the
bank, every number must come from the bank, and skill lines must only be
reorderings. The number of variants checked is recorded for the README.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from jobpulse.bank import parse_bank
from jobpulse.context import demo_bank_toml
from jobpulse.models import Extraction
from jobpulse.pipeline.render import render_one_page
from jobpulse.pipeline.tailor import tailor, verify_truthful
from jobpulse.taxonomy import default_taxonomy

TAX = default_taxonomy()
SKILLS = sorted(TAX.skills)
EXAMPLES = int(os.environ.get("TRUTH_EXAMPLES", "300"))
CHECKED = {"demo_bank": 0, "random_bank": 0}

extractions = st.builds(
    lambda req, nice: Extraction(tuple(req), tuple(s for s in nice if s not in req), "", (), (), "test"),
    st.lists(st.sampled_from(SKILLS), max_size=12, unique=True),
    st.lists(st.sampled_from(SKILLS), max_size=8, unique=True),
)

words = st.sampled_from(
    [
        "Built",
        "Cut",
        "latency",
        "from",
        "to",
        "with",
        "Python",
        "Kafka",
        "AWS",
        "React",
        "PyTorch",
        "tests",
        "Go,",
        "SQL",
        "Docker",
        "Raft",
        "users",
        "requests/s",
    ]
)
numbers = st.sampled_from(["3", "12", "95%", "2.8 s", "10,000", "$1", "0.78", "250 ms"])
bullet_text = st.lists(st.one_of(words, numbers), min_size=4, max_size=28).map(" ".join)


@st.composite
def banks(draw: st.DrawFn) -> str:
    lines = ['[profile]\nname = "Random Person"\n']
    for g in range(draw(st.integers(1, 6))):
        items = draw(st.lists(st.sampled_from(SKILLS), min_size=1, max_size=8, unique=True))
        lines.append(f'[skills.g{g}]\nlabel = "Group {g}"\nitems = {json.dumps(items)}\n')
    for p in range(draw(st.integers(1, 7))):
        lines.append(f'[projects.p{p}]\nname = "Project {p}"\ndesc = "Desc {p}"\n[projects.p{p}.bullets]')
        for b in range(draw(st.integers(1, 5))):
            lines.append(f"b{b} = {json.dumps(draw(bullet_text))}")
        lines.append("")
    return "\n".join(lines)


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(extractions)
def test_demo_bank_resumes_are_verbatim(extraction: Extraction) -> None:
    bank = parse_bank(demo_bank_toml())
    resume = tailor(bank, extraction, TAX)
    result, _ = render_one_page(bank, resume)
    verify_truthful(resume, bank)
    assert result.fits
    CHECKED["demo_bank"] += 1


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(banks(), extractions)
def test_random_bank_resumes_are_verbatim(bank_toml: str, extraction: Extraction) -> None:
    bank = parse_bank(bank_toml)
    resume = tailor(bank, extraction, TAX)
    render_one_page(bank, resume)  # the overflow guard may drop bullets; it must never edit them
    verify_truthful(resume, bank)
    CHECKED["random_bank"] += 1


def teardown_module() -> None:
    out = os.environ.get("TRUTH_REPORT")
    if out:
        Path(out).write_text(json.dumps(CHECKED))
