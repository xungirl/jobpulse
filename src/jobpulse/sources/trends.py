"""Developer-attention signals: new GitHub repos by stars, and Hacker News stories.

Both are public, documented APIs (GitHub REST search: 10 requests/min
unauthenticated; HN Algolia search). We make ONE request to each per run.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from jobpulse.ports import HttpClient

GITHUB_SEARCH = "https://api.github.com/search/repositories"
HN_SEARCH = "https://hn.algolia.com/api/v1/search"


@dataclass(frozen=True)
class AttentionItem:
    source: str  # "github" | "hn"
    text: str  # repo name + description + topics + language, or story title
    weight: int  # stars or points
    url: str


def github_new_repos(http: HttpClient, run_date: str, token: str | None = None) -> list[AttentionItem]:
    end = date.fromisoformat(run_date)
    start = end - timedelta(days=7)
    q = f"created:{start.isoformat()}..{end.isoformat()} stars:>10"
    params = {"q": q, "sort": "stars", "order": "desc", "per_page": 100}
    url = f"{GITHUB_SEARCH}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.loads(http.get_text(url, headers))
    items = []
    for r in data.get("items", []):
        text = " ".join(
            filter(
                None,
                [
                    r.get("name", "").replace("-", " "),
                    r.get("description") or "",
                    " ".join(r.get("topics") or []),
                    r.get("language") or "",
                ],
            )
        )
        items.append(AttentionItem("github", text, int(r.get("stargazers_count", 0)), r.get("html_url", "")))
    return items


def hn_stories(http: HttpClient, run_date: str, min_points: int = 30) -> list[AttentionItem]:
    end = datetime.combine(date.fromisoformat(run_date), datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    start = end - timedelta(days=7)
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{int(start.timestamp())},created_at_i<{int(end.timestamp())},"
        f"points>{min_points}",
        "hitsPerPage": 500,
    }
    data = json.loads(http.get_text(f"{HN_SEARCH}?{urllib.parse.urlencode(params)}"))
    return [
        AttentionItem(
            "hn",
            h.get("title") or "",
            int(h.get("points") or 0),
            f"https://news.ycombinator.com/item?id={h.get('objectID')}",
        )
        for h in data.get("hits", [])
    ]
