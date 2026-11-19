"""Step 1 - ingest: new Summer 2027 postings + full JD text + rule-based constraints."""

from __future__ import annotations

from jobpulse.context import Context
from jobpulse.models import Posting
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.pipeline.constraints import find_constraints
from jobpulse.ports import HttpError
from jobpulse.sources import ats, simplify


def run_ingest(ctx: Context, run_date: str) -> StepResult:
    m = Metrics()
    raw = simplify.fetch_listings(ctx.http)
    m.set("listings_total", len(raw))
    listings = simplify.dedupe(simplify.parse_listings(raw, run_date, ctx.settings.lookback_days))
    m.set("listings_matching_filter", len(listings))

    # Skip postings first seen on an earlier run; keep ones first seen today (re-run = rewrite).
    fresh = [x for x in listings if ctx.repos.first_seen(x.id) in (None, run_date)]
    fresh.sort(key=lambda x: (x.date_posted, x.id), reverse=True)  # newest first, deterministic
    selected = fresh[: ctx.settings.max_postings_per_run]
    m.set("listings_new", len(fresh))
    m.set("listings_capped", len(fresh) - len(selected))

    for listing in selected:
        try:
            job = ats.fetch_job_text(ctx.http, listing.url) if ats.supported(listing.url) else None
        except HttpError as e:
            m.incr("jd_fetch_failed")
            ctx.log.warning("jd_fetch_failed", posting=listing.id, status=e.status, reason=e.reason[:120])
            job = None
        except (KeyError, ValueError, TypeError) as e:  # unexpected JSON shape from one ATS
            m.incr("jd_fetch_failed")
            ctx.log.warning("jd_parse_failed", posting=listing.id, error=type(e).__name__)
            job = None

        if job is not None and not job.open:
            m.incr("jd_closed")
            continue
        if job is None and not ats.supported(listing.url):
            m.incr("jd_unsupported_ats")

        text = job.text if job else ""
        posting = Posting(
            id=listing.id,
            company=listing.company,
            title=listing.title,
            url=listing.url,
            locations=listing.locations,
            category=listing.category,
            date_posted=listing.date_posted,
            first_seen=run_date,
            ats=job.ats if job else None,
            jd_text=text,
            sponsorship=listing.sponsorship,
            constraints=find_constraints(text),
        )
        if ctx.repos.record_posting(posting):
            m.incr("postings_ingested")
            if text:
                m.incr("postings_with_jd")

    ctx.log.info("ingest_done", run_date=run_date, **m.counters)
    return StepResult("ingest", run_date, m.counters)
