"""Hard eligibility constraints, found by explicit rules (auditable, never by the LLM).

These flags gate whether a posting is worth tailoring for; a false negative
here costs an application, so each rule is a readable regex with a test.
"""

from __future__ import annotations

import re

from jobpulse.models import Constraints

_DEGREES = {
    "Bachelor's": re.compile(r"\b(bachelor'?s?|b\.?s\.?|undergraduate|BA/BS|BS/MS)\b", re.I),
    "Master's": re.compile(r"\b(master'?s?|m\.?s\.?|graduate student|MS/PhD|BS/MS)\b", re.I),
    "PhD": re.compile(r"\b(ph\.?\s?d\.?|doctoral|doctorate)\b", re.I),
}
_PHD_ONLY = re.compile(
    r"\b(currently (enrolled|pursuing)( in)? an? (ph\.?\s?d|doctoral)|ph\.?\s?d\.? (student|candidate)s? only|"
    r"must be (a|enrolled in a) ph\.?\s?d)", re.I
)
_GRAD_WINDOW = re.compile(
    r"graduat\w*[^.\n]{0,40}?\b((?:between|from|in|by|before|after)\s+)?"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|fall|spring|summer|winter)\w*\.?\s+20(2[6-9])"
    r"(?:\s*(?:-|–|and|to|through)\s*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|fall|spring|summer|"
    r"winter)\w*\.?\s+)?20(2[6-9]))?)",
    re.I,
)
_NO_SPONSORSHIP = re.compile(
    r"(not|unable to|cannot|can't|won't|will not|does not|do not)\s+(be\s+able\s+to\s+)?"
    r"(provide|offer|support|sponsor)\w*[^.\n]{0,60}?(sponsor|visa|work authori[sz]ation)"
    r"|without\s+(the\s+need\s+for\s+)?(current\s+or\s+future\s+|future\s+)?(visa\s+)?sponsorship"
    r"|no\s+(visa\s+)?sponsorship"
    r"|sponsorship\s+(is\s+)?not\s+(available|provided|offered)",
    re.I,
)
_CITIZENSHIP = re.compile(
    r"(must\s+be\s+a\s+u\.?s\.?\s+citizen|u\.?s\.?\s+citizenship\s+(is\s+)?required|"
    r"requires?\s+u\.?s\.?\s+citizenship|\bITAR\b|\bU\.?S\.?\s+persons?\b|EAR\s+export)",
    re.I,
)
_CLEARANCE = re.compile(r"(security\s+clearance|\bTS/SCI\b|secret\s+clearance|active\s+clearance)", re.I)


def find_constraints(jd_text: str) -> Constraints:
    degrees = tuple(name for name, rx in _DEGREES.items() if rx.search(jd_text))
    window = _GRAD_WINDOW.search(jd_text)
    return Constraints(
        degree_levels=degrees,
        requires_phd=bool(_PHD_ONLY.search(jd_text)) or degrees == ("PhD",),
        grad_window=window.group(2).strip() if window else None,
        no_sponsorship=bool(_NO_SPONSORSHIP.search(jd_text)),
        citizenship_required=bool(_CITIZENSHIP.search(jd_text)),
        clearance_required=bool(_CLEARANCE.search(jd_text)),
    )
