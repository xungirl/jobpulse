"""Runs pipeline steps and keeps the per-date RunRecord.

`run_step` is the single entry point used by BOTH the local CLI and the AWS
Lambda handlers, so orchestration differs (a Python loop vs Step Functions)
but bookkeeping, metrics and failure handling are identical.

Failure policy - "degrade, don't die": a failing step is recorded and the next
steps still run on whatever data exists, so the dashboard is always published.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from jobpulse.context import Context
from jobpulse.models import RunRecord
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.pipeline.extract import run_extract
from jobpulse.pipeline.gaps import run_gaps
from jobpulse.pipeline.ingest import run_ingest
from jobpulse.pipeline.market import run_trends
from jobpulse.pipeline.publish import run_publish
from jobpulse.pipeline.resumes import run_tailor

STEPS: dict[str, Callable[[Context, str], StepResult]] = {
    "ingest": run_ingest,
    "extract": run_extract,
    "trends": run_trends,
    "tailor": run_tailor,
    "gaps": run_gaps,
    "publish": run_publish,
}
ORDER = list(STEPS)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _final_status(run: RunRecord) -> str:
    return "succeeded" if all(v == "ok" for v in run.steps.values()) else "partial"


def run_step(ctx: Context, step: str, run_date: str) -> StepResult:
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; expected one of {ORDER}")
    run = ctx.repos.run(run_date)
    if run is None or step == ORDER[0]:
        run = RunRecord(date=run_date, status="running", started_at=_now())  # (re)start: idempotent overwrite
    if step == ORDER[-1]:
        run.status, run.finished_at = _final_status(run), _now()  # publish shows this run as finished
    ctx.repos.put_run(run)

    log = ctx.log.bind(run_date=run_date, step=step)
    metrics = Metrics()
    try:
        result = STEPS[step](ctx, run_date)
    except Exception as e:
        run.steps[step] = f"failed: {type(e).__name__}"
        run.status = "failed" if step == ORDER[-1] else run.status
        ctx.repos.put_run(run)
        metrics.set("step_failed", 1)
        metrics.flush(step, run_date, emf=ctx.settings.emf_metrics)
        log.error("step_failed", error=f"{type(e).__name__}: {e}"[:300])
        raise
    run.steps[step] = "ok"
    run.metrics.update({f"{step}.{k}": v for k, v in result.metrics.items()})
    if step == ORDER[-1]:
        run.status = _final_status(run)
    ctx.repos.put_run(run)
    metrics.merge(result.metrics)
    metrics.set("duration_s", metrics.elapsed_s())
    metrics.flush(step, run_date, emf=ctx.settings.emf_metrics)
    return result


def run_pipeline(ctx: Context, run_date: str, steps: list[str] | None = None) -> RunRecord:
    for step in steps or ORDER:
        try:
            run_step(ctx, step, run_date)
        except Exception:
            continue
    run = ctx.repos.run(run_date)
    assert run is not None
    return run
