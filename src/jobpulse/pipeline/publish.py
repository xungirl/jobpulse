"""Step 6 - publish: write the static dashboard (HTML/JS/CSS + one JSON data file)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from typing import Any

from jobpulse.context import Context
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.pipeline.linkedin import weekly_post
from jobpulse.pipeline.market import demand_table

STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    # Instrument Serif (SIL OFL 1.1), Latin subset, self-hosted so the page makes no third-party requests.
    "fonts/instrument-serif-regular.woff2": "font/woff2",
    "fonts/instrument-serif-italic.woff2": "font/woff2",
    "fonts/OFL.txt": "text/plain; charset=utf-8",
}
HISTORY_DAYS = 30
CHART_SKILLS = 6

# Project roadmap shown in the dashboard's second timeline (status: done | todo).
ROADMAP = [
    (
        "done",
        "Stage 1-2",
        "Ingest",
        "Simplify listings + public ATS APIs, dedupe, rule-based eligibility flags.",
    ),
    (
        "done",
        "Stage 3",
        "LLM extraction + eval",
        "Validated JSON, retries, keyword fallback, labeled eval set.",
    ),
    ("done", "Stage 4", "Market & trends", "Daily demand share, 7/30-day deltas, GitHub + HN attention."),
    ("done", "Stage 5", "Truthful tailoring", "Select-and-reorder only; property-tested invariant."),
    ("done", "Stage 6-7", "Gaps, ideas, dashboard", "Demand x trend ranking, 3 ideas/day, this page."),
    (
        "done",
        "Stage 8-10",
        "AWS, observability, CI",
        "CDK stack, EMF metrics, idempotent runs, GitHub Actions.",
    ),
    ("todo", "Next", "More sources", "Add other public listing repos; per-company ATS coverage report."),
    ("todo", "Next", "Better trend signal", "Weight GitHub stars by recency; add arXiv for ML topics."),
]


def _series(ctx: Context, run_date: str, skills: list[str]) -> dict[str, Any]:
    series = ctx.repos.demand_series()
    end = date.fromisoformat(run_date)
    days = [(end - timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS - 1, -1, -1)]
    out: dict[str, Any] = {"dates": days, "postings": [], "skills": {s: [] for s in skills}}
    for d in days:
        doc = series.get(d)
        n = int(doc["postings"]) if doc else 0
        out["postings"].append(n)
        for s in skills:
            out["skills"][s].append(int(doc["counts"].get(s, 0)) if doc else 0)
    return out


def build_dashboard_data(ctx: Context, run_date: str) -> dict[str, Any]:
    postings = []
    day = sorted(
        ctx.repos.postings_for_day(run_date), key=lambda p: (not p.jd_text, p.company.lower(), p.title)
    )
    for p in day:  # postings with a full JD first
        ex = ctx.repos.posting_extraction(p.id)
        c = p.constraints
        flags = [
            f
            for f, on in (
                ("PhD only", c.requires_phd),
                ("no sponsorship", c.no_sponsorship),
                ("US citizenship", c.citizenship_required),
                ("clearance", c.clearance_required),
            )
            if on
        ]
        postings.append(
            {
                "company": p.company,
                "title": p.title,
                "url": p.url,
                "locations": list(p.locations[:3]),
                "date_posted": p.date_posted,
                "has_jd": bool(p.jd_text),
                "required": list(ex.required_skills) if ex else [],
                "nice": list(ex.nice_to_have) if ex else [],
                "method": ex.method if ex else None,
                "flags": flags,
                "grad_window": c.grad_window,
            }
        )
    table = demand_table(ctx.repos.demand_series(), run_date, ctx.taxonomy)
    report = ctx.repos.report(run_date)
    runs = sorted(ctx.repos.runs(), key=lambda r: r.date)[-HISTORY_DAYS:]
    ideas = sorted(ctx.repos.all_ideas(), key=lambda i: i.date, reverse=True)
    return {
        "run_date": run_date,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "persona_note": "Resumes are generated for a fictional demo persona; every bullet is copied verbatim "
        "from its bullet bank.",
        "postings": postings,
        "demand": table[:25],
        "series": _series(ctx, run_date, [r["skill"] for r in table[:CHART_SKILLS]]),
        "trends": [s.to_dict() for s in ctx.repos.trends(run_date)[:15]],
        "trends_meta": ctx.repos.trends_meta(run_date),
        "gaps": report.get("gaps", [])[:10],
        "ideas_today": [i.to_dict() for i in ideas if i.date == run_date],
        "ideas_recent": [i.to_dict() for i in ideas if i.date != run_date][:9],
        "resumes": sorted(ctx.repos.resumes(run_date), key=lambda r: -r["coverage_after"]),
        "runs": [{"date": r.date, "status": r.status, "metrics": r.metrics} for r in runs],
        "roadmap": [{"status": s, "label": lbl, "title": t, "desc": d} for s, lbl, t, d in ROADMAP],
        "linkedin_draft": weekly_post(ctx.repos, run_date, ctx.taxonomy),
    }


def run_publish(ctx: Context, run_date: str) -> StepResult:
    m = Metrics()
    web = resources.files("jobpulse.web")
    for name, ctype in STATIC_FILES.items():
        ctx.site.put_bytes(name, web.joinpath(name).read_bytes(), ctype, cache_seconds=300)
    data = build_dashboard_data(ctx, run_date)
    payload = json.dumps(data, indent=1).encode("utf-8")
    ctx.site.put_bytes("data/dashboard.json", payload, "application/json", cache_seconds=60)
    ctx.site.put_bytes(f"data/history/{run_date}.json", payload, "application/json", cache_seconds=86400)
    ctx.site.put_bytes(
        "linkedin-draft.txt", data["linkedin_draft"].encode("utf-8"), "text/plain; charset=utf-8"
    )
    m.set("postings_published", len(data["postings"]))
    m.set("resumes_linked", len(data["resumes"]))
    ctx.log.info("publish_done", run_date=run_date, **m.counters)
    return StepResult("publish", run_date, m.counters)
