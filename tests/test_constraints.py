"""Rule-based eligibility flags. Phrases come from real JDs seen during development."""

from __future__ import annotations

import pytest

from jobpulse.pipeline.constraints import find_constraints


@pytest.mark.parametrize(
    ("text", "phd_only"),
    [
        ("Must be currently enrolled in a PhD program in Computer Science", True),
        ("Currently pursuing a Ph.D. or equivalent experience in Computer Science", True),
        ("Currently enrolled in a PhD or MS program in Computer Science", False),
        ("currently pursuing a PhD or MS in Computer Science", False),
        ("Pursuing a BS, MS or PhD", False),
    ],
)
def test_phd_only(text: str, phd_only: bool) -> None:
    assert find_constraints(text).requires_phd is phd_only


@pytest.mark.parametrize(
    ("text", "flag"),
    [
        ("Must be a U.S. Person due to required access to U.S. export-controlled technology", True),
        ("This candidate must be a U.S. citizen (U.S. born or naturalized)", True),
        ("requires the successful candidate to be a U.S. Person (U.S. citizen, permanent resident)", True),
        ("ITAR REQUIREMENT This position requires access to ITAR data", True),
        ("US Citizenship Required:  No    Job Description", False),
        ("provide documentation that establishes status as a U.S. person or non-U.S. person", False),
    ],
)
def test_citizenship(text: str, flag: bool) -> None:
    assert find_constraints(text).citizenship_required is flag


@pytest.mark.parametrize(
    "text",
    [
        "DriveWealth is unable to sponsor an employment Visa at this time.",
        "This position does not offer sponsorship for employment visas.",
        "Authorization to work in the United States without the need for sponsorship now or in the future.",
        "GM DOES NOT PROVIDE IMMIGRATION-RELATED SPONSORSHIP FOR THIS ROLE.",
        "Sponsorship is not available for this role.",
    ],
)
def test_no_sponsorship(text: str) -> None:
    assert find_constraints(text).no_sponsorship


def test_sponsorship_offered_is_not_flagged() -> None:
    assert not find_constraints("We sponsor visas for interns and offer relocation.").no_sponsorship


@pytest.mark.parametrize(
    ("text", "window"),
    [
        (
            "with an expected graduation date between December 2027 and August 2028.",
            "December 2027 and August 2028",
        ),
        ("Expected to graduate between Fall 2027 and Summer 2028, and enrolled", "Fall 2027 and Summer 2028"),
        ("A graduation date between December 2026-July 2028.", "December 2026-July 2028"),
        ("enrolled in an undergraduate or graduate degree program, returning to school in Fall 2027", None),
    ],
)
def test_grad_window(text: str, window: str | None) -> None:
    assert find_constraints(text).grad_window == window


def test_degrees_and_clearance() -> None:
    c = find_constraints("Pursuing a Bachelor's or Master's degree. Ability to obtain a Secret clearance.")
    assert c.degree_levels == ("Bachelor's", "Master's")
    assert c.clearance_required
    assert not c.requires_phd
