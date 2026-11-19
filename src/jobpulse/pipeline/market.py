"""Step 3 - trends: daily skill demand, 7/30-day deltas, and trending-tech signals.

Demand is measured as the SHARE of postings that mention a skill (required or
nice-to-have), so days with more postings do not look like rising demand.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from jobpulse.context import Context
from jobpulse.models import TrendSignal
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.ports import HttpError
from jobpulse.sources.trends import AttentionItem, github_new_repos, hn_stories
from jobpulse.taxonomy import Taxonomy


def daily_demand(ctx: Context, run_date: str) -> tuple[int, Counter[str], Counter[str]]:
    any_counts: Counter[str] = Counter()
    required_counts: Counter[str] = Counter()
    n = 0
    for p in ctx.repos.postings_for_day(run_date):
        ex = ctx.repos.posting_extraction(p.id)
        if ex is None:
            continue
        n += 1
        any_counts.update(ex.all_skills)
        required_counts.update(set(ex.required_skills))
    return n, any_counts, required_counts


def window_share(series: dict[str, dict[str, Any]], end: str, days: int) -> tuple[int, dict[str, float]]:
    """Postings and per-skill share over the `days` days ending at `end` (inclusive)."""
    end_d = date.fromisoformat(end)
    start = (end_d - timedelta(days=days - 1)).isoformat()
    total, counts = 0, Counter[str]()
    for d, doc in series.items():
        if start <= d <= end:
            total += int(doc["postings"])
            counts.update({k: int(v) for k, v in doc["counts"].items()})
    return total, ({k: v / total for k, v in counts.items()} if total else {})


def demand_table(series: dict[str, dict[str, Any]], run_date: str, tax: Taxonomy) -> list[dict[str, Any]]:
    """Per-skill demand with deltas (percentage points of share) vs the previous window."""
    end = date.fromisoformat(run_date)
    rows: dict[str, dict[str, Any]] = {}
    for days in (7, 30):
        n_now, now = window_share(series, run_date, days)
        n_prev, prev = window_share(series, (end - timedelta(days=days)).isoformat(), days)
        for skill in set(now) | set(prev):
            r = rows.setdefault(skill, {"skill": skill, "category": tax.category(skill)})
            r[f"share_{days}d"] = round(now.get(skill, 0.0), 4)
            # A delta needs postings in both windows; otherwise it is unknown, not zero.
            r[f"delta_{days}d"] = (
                round(now.get(skill, 0.0) - prev.get(skill, 0.0), 4) if n_now and n_prev else None
            )
            r[f"postings_{days}d"] = n_now
    _, counts30 = window_share(series, run_date, 30)
    for skill, r in rows.items():
        r["count_30d"] = round(counts30.get(skill, 0.0) * r.get("postings_30d", 0))
    return sorted(rows.values(), key=lambda r: (-r.get("share_30d", 0), r["skill"]))


def trend_signals(items: list[AttentionItem], tax: Taxonomy) -> list[TrendSignal]:
    agg: dict[str, list[int]] = {}
    for it in items:
        for skill in tax.find(it.text):
            a = agg.setdefault(skill, [0, 0, 0, 0])
            if it.source == "github":
                a[0] += 1
                a[1] += it.weight
            else:
                a[2] += 1
                a[3] += it.weight
    max_gh = max((a[0] for a in agg.values()), default=0) or 1
    max_hn = max((a[2] for a in agg.values()), default=0) or 1
    signals = [
        TrendSignal(skill, a[0], a[1], a[2], a[3], round(0.5 * a[0] / max_gh + 0.5 * a[2] / max_hn, 4))
        for skill, a in agg.items()
    ]
    return sorted(signals, key=lambda s: (-s.score, s.skill))


def run_trends(ctx: Context, run_date: str) -> StepResult:
    m = Metrics()
    n, any_counts, required_counts = daily_demand(ctx, run_date)
    ctx.repos.put_demand(run_date, n, dict(any_counts))
    m.set("postings_with_extraction", n)
    m.set("skills_seen", len(any_counts))

    items: list[AttentionItem] = []
    meta: dict[str, Any] = {}
    for name in ("github", "hn"):
        try:
            if name == "github":
                got = github_new_repos(ctx.http, run_date, ctx.settings.github_token)
            else:
                got = hn_stories(ctx.http, run_date)
            items += got
            meta[f"{name}_items"] = len(got)
        except (HttpError, ValueError, KeyError) as e:  # trends are optional; never fail the run
            m.incr("trend_source_failures")
            meta[f"{name}_error"] = str(e)[:120]
            ctx.log.warning("trend_source_failed", source=name, error=str(e)[:120])
    signals = trend_signals(items, ctx.taxonomy)
    ctx.repos.put_trends(run_date, signals, meta)
    m.set("trend_items", len(items))
    m.set("trend_skills", len(signals))

    table = demand_table(ctx.repos.demand_series(), run_date, ctx.taxonomy)
    report = ctx.repos.report(run_date)
    report.update(demand=table[:40], required_today=dict(required_counts.most_common(20)), postings_today=n)
    ctx.repos.put_report(run_date, report)
    ctx.log.info("trends_done", run_date=run_date, **m.counters)
    return StepResult("trends", run_date, m.counters)
