"""Step 4 - tailor: one truthful one-page resume per eligible new posting."""

from __future__ import annotations

import re
from typing import Any

from jobpulse.bank import Bank, parse_bank
from jobpulse.context import Context
from jobpulse.models import Constraints
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.pipeline.render import render_one_page
from jobpulse.pipeline.tailor import coverage, default_resume, tailor, verify_truthful


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def ineligible_reason(c: Constraints, bank: Bank) -> str | None:
    """Hard constraints vs the candidate's [profile.eligibility] (all optional)."""
    elig: dict[str, Any] = bank.profile.get("eligibility", {})
    if c.requires_phd and elig.get("degree", "Master's") != "PhD":
        return "requires PhD"
    if c.clearance_required and not elig.get("has_clearance", False):
        return "security clearance"
    if c.citizenship_required and not elig.get("us_citizen", False):
        return "US citizenship"
    if c.no_sponsorship and elig.get("needs_sponsorship", False):
        return "no sponsorship"
    return None


def run_tailor(ctx: Context, run_date: str) -> StepResult:
    m = Metrics()
    bank = parse_bank(ctx.bank_toml)
    baseline = default_resume(bank)
    ctx.repos.clear_resumes(run_date)  # re-running a date replaces its resumes

    candidates = []
    for p in ctx.repos.postings_for_day(run_date):
        ex = ctx.repos.posting_extraction(p.id)
        if ex is None or not ex.all_skills:
            continue
        reason = ineligible_reason(p.constraints, bank)
        if reason:
            m.incr("skipped_ineligible")
            continue
        resume = tailor(bank, ex, ctx.taxonomy)
        candidates.append((coverage(resume, ex, ctx.taxonomy), p, ex, resume))

    candidates.sort(key=lambda t: (-t[0], t[1].id))  # best fit first
    before_sum = after_sum = 0.0
    for _, p, ex, resume in candidates[: ctx.settings.max_resumes_per_run]:
        try:
            result, dropped = render_one_page(bank, resume, title=f"{p.company} {p.title}")
            verify_truthful(resume, bank)  # raises if any claim is not verbatim from the bank
        except Exception as e:  # one bad posting must not kill the run
            m.incr("tailor_failures")
            ctx.log.error("tailor_failed", posting=p.id, error=f"{type(e).__name__}: {e}"[:200])
            continue
        m.incr("truthfulness_checks_passed")
        m.incr("overflow_bullets_dropped", dropped)
        if not result.fits:
            m.incr("overflow_unresolved")
        key = f"resumes/{run_date}/{slug(p.company)}-{p.id[:8]}.pdf"
        ctx.site.put_bytes(key, result.pdf, "application/pdf", cache_seconds=86400)
        cov_before, cov_after = coverage(baseline, ex, ctx.taxonomy), coverage(resume, ex, ctx.taxonomy)
        before_sum += cov_before
        after_sum += cov_after
        ctx.repos.put_resume(
            run_date,
            p.id,
            {
                "posting_id": p.id,
                "company": p.company,
                "title": p.title,
                "url": p.url,
                "pdf": key,
                "coverage_before": round(cov_before, 4),
                "coverage_after": round(cov_after, 4),
                "projects": [c.name for c in resume.projects],
                "bullets": len(resume.all_bullets()),
                "fits_one_page": result.fits,
            },
        )
        m.incr("resumes_generated")

    n = m.counters.get("resumes_generated", 0)
    if n:
        m.set("coverage_before_avg", round(before_sum / n, 4))
        m.set("coverage_after_avg", round(after_sum / n, 4))
    ctx.log.info("tailor_done", run_date=run_date, **m.counters)
    return StepResult("tailor", run_date, m.counters)
