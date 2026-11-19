"""SimplifyJobs Summer 2027 internship listings (public JSON in a GitHub repo)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from jobpulse.ports import HttpClient

LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
)
TERM = "Summer 2027"
SWE_CATEGORIES = {"Software", "Software Engineering"}
AI_CATEGORIES = {"AI/ML/Data", "Data Science, AI & Machine Learning"}
AI_TITLE = re.compile(
    r"\b(machine learning|ml|ai|artificial intelligence|llm|deep learning|nlp|computer vision|"
    r"software|engineer(ing)?|research)\b",
    re.IGNORECASE,
)
US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
    "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


@dataclass(frozen=True)
class Listing:
    id: str
    company: str
    title: str
    url: str
    locations: tuple[str, ...]
    category: str
    date_posted: str
    sponsorship: str | None


def is_us_location(loc: str) -> bool:
    loc = loc.strip()
    if re.search(r"\b(USA|United States|U\.S\.)\b", loc) or loc in {"Remote", "Remote in US"}:
        return True
    m = re.search(r",\s*([A-Z]{2})$", loc)
    return bool(m and m.group(1) in US_STATES)


def is_target_role(category: str, title: str) -> bool:
    if category in SWE_CATEGORIES:
        return True
    return category in AI_CATEGORIES and bool(AI_TITLE.search(title))


def _utc_day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).date().isoformat()


def parse_listings(raw: list[dict[str, Any]], run_date: str, lookback_days: int) -> list[Listing]:
    """Active, visible Summer 2027 SWE/AI/ML postings in the US, posted in [D - lookback, D]."""
    end = date.fromisoformat(run_date)
    start = (end - timedelta(days=lookback_days)).isoformat()
    out = []
    for x in raw:
        if not (x.get("active") and x.get("is_visible", True)):
            continue
        if TERM not in (x.get("terms") or []):
            continue
        posted = _utc_day(int(x.get("date_posted", 0)))
        if not (start <= posted <= run_date):
            continue
        if not is_target_role(x.get("category", ""), x.get("title", "")):
            continue
        locations = tuple(x.get("locations") or ())
        if not any(is_us_location(loc) for loc in locations):
            continue
        out.append(Listing(
            id=str(x["id"]),
            company=str(x.get("company_name", "")).strip(),
            title=str(x.get("title", "")).strip(),
            url=str(x["url"]),
            locations=locations,
            category=str(x.get("category", "")),
            date_posted=posted,
            sponsorship=x.get("sponsorship"),
        ))
    out.sort(key=lambda listing: (listing.date_posted, listing.company, listing.id))
    return out


def dedupe(listings: list[Listing]) -> list[Listing]:
    """Drop repeats of the same role (multi-location copies): same company + normalized title."""
    seen: set[tuple[str, str]] = set()
    out = []
    for listing in listings:
        key = (listing.company.lower(), re.sub(r"\W+", " ", listing.title.lower()).strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(listing)
    return out


def fetch_listings(http: HttpClient) -> list[dict[str, Any]]:
    data = json.loads(http.get_text(LISTINGS_URL))
    if not isinstance(data, list):
        raise ValueError("unexpected listings.json shape")
    return data
