"""Weekly LinkedIn post DRAFT (text only). Never posts anything anywhere.

Every number in the draft is read from stored pipeline results.
"""

from __future__ import annotations

from datetime import date, timedelta

from jobpulse.pipeline.market import demand_table, window_share
from jobpulse.repositories import Repos
from jobpulse.taxonomy import Taxonomy


def weekly_post(repos: Repos, week_end: str, tax: Taxonomy) -> str:
    series = repos.demand_series()
    n7, share7 = window_share(series, week_end, 7)
    start = (date.fromisoformat(week_end) - timedelta(days=6)).isoformat()
    runs = [r for r in repos.runs() if start <= r.date <= week_end and r.status == "succeeded"]
    if not n7:
        return f"(no postings with extracted requirements in the week ending {week_end}; nothing to post)"

    top = sorted(share7.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    table = demand_table(series, week_end, tax)
    risers = sorted(
        (r for r in table if r.get("delta_7d") is not None and r.get("share_7d", 0) > 0),
        key=lambda r: -r["delta_7d"],
    )[:3]
    trends = repos.trends(week_end)[:3]

    lines = [
        f"What Summer 2027 SWE/AI internship postings asked for this week (week ending {week_end}):",
        "",
        f"My pipeline read {n7} new US postings with full job descriptions across {len(runs)} daily runs.",
        "Most-requested skills (share of postings):",
        *[f"- {skill}: {share:.0%}" for skill, share in top],
    ]
    if risers and risers[0]["delta_7d"] > 0:
        lines += [
            "",
            "Rising vs the previous week:",
            *[f"- {r['skill']}: {r['delta_7d'] * 100:+.1f} pp" for r in risers if r["delta_7d"] > 0],
        ]
    if trends:
        lines += [
            "",
            "Developer attention (new GitHub repos + Hacker News): "
            + ", ".join(t.skill for t in trends)
            + ".",
        ]
    lines += [
        "",
        "Caveats: one source (SimplifyJobs listings), only postings whose ATS exposes a public JSON API, "
        "and skills are LLM-extracted (precision/recall measured in the repo).",
        "",
        "#internships #softwareengineering #machinelearning",
    ]
    return "\n".join(lines)
