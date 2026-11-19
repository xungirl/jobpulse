"""Integration: the whole pipeline on fixtures (fake HTTP + fake LLM), idempotency, partial failure."""

from __future__ import annotations

import io
import json
from pathlib import Path

from jobpulse.adapters.http import FakeHttpClient
from jobpulse.adapters.llm import FakeProvider
from jobpulse.context import Context
from jobpulse.observability import EMF_METRICS, JsonLogger, Metrics
from jobpulse.pipeline.gaps import is_duplicate, rank_gaps
from jobpulse.pipeline.linkedin import weekly_post
from jobpulse.runner import run_pipeline, run_step


def snapshot(ctx: Context) -> dict[tuple[str, str], object]:
    store = ctx.repos.store
    items: dict[tuple[str, str], dict[str, object]] = store.items  # type: ignore[attr-defined]
    volatile = ("RUN",)  # timestamps differ between runs by design
    return {k: v for k, v in items.items() if k[0] not in volatile}


def test_full_pipeline_on_fixtures(ctx: Context, tmp_path: Path) -> None:
    run = run_pipeline(ctx, "2026-11-18")
    assert run.status == "succeeded", run.steps
    assert run.metrics["ingest.postings_ingested"] == 5
    assert run.metrics["ingest.postings_with_jd"] == 3
    assert run.metrics["ingest.jd_fetch_failed"] == 1  # the broken greenhouse JSON did not kill the run
    assert run.metrics["ingest.jd_unsupported_ats"] == 1

    data = json.loads((tmp_path / "site" / "data" / "dashboard.json").read_text())
    assert len(data["postings"]) == 5 and data["run_date"] == "2026-11-18"
    assert {"Python", "Kubernetes"} <= {r["skill"] for r in data["demand"]}
    assert data["trends"] and data["roadmap"]
    assert (tmp_path / "site" / "index.html").exists() and (tmp_path / "site" / "app.js").exists()
    for r in data["resumes"]:
        assert (tmp_path / "site" / r["pdf"]).read_bytes().startswith(b"%PDF")
    # Only Rust and Robotics are demanded twice and not evidenced in the demo bank -> 2 ideas, one per gap.
    assert run.metrics["gaps.gaps_found"] == 2
    assert sorted(i["target_skills"][0] for i in data["ideas_today"]) == ["Robotics", "Rust"]
    assert all("Measured demand" in i["why"] for i in data["ideas_today"])
    acme = next(p for p in data["postings"] if p["company"] == "Acme Robotics")
    assert "no sponsorship" in acme["flags"] and acme["grad_window"] == "December 2027 and June 2028"


def test_rerunning_a_date_is_idempotent(ctx: Context) -> None:
    run_pipeline(ctx, "2026-11-18")
    first = snapshot(ctx)
    run_pipeline(ctx, "2026-11-18")
    assert snapshot(ctx) == first
    assert len(ctx.repos.postings_for_day("2026-11-18")) == 5
    assert len(ctx.repos.runs()) == 1


def test_next_day_does_not_reingest_yesterdays_postings(ctx: Context) -> None:
    run_pipeline(ctx, "2026-11-18")
    run = run_pipeline(ctx, "2026-11-19")
    assert run.metrics.get("ingest.postings_ingested", 0) == 0


def test_failing_step_degrades_but_publish_still_runs(ctx: Context) -> None:
    def broken(system: str, user: str, purpose: str) -> str:
        raise RuntimeError("unexpected bug inside the provider")

    ctx.llm = FakeProvider(broken)  # RuntimeError is not an LLMError -> the extract step itself fails
    run = run_pipeline(ctx, "2026-11-18")
    assert run.steps["extract"].startswith("failed")
    assert run.steps["publish"] == "ok"
    assert run.status == "partial"


def test_source_outage_is_recorded_and_dashboard_still_published(ctx: Context, tmp_path: Path) -> None:
    ctx.http = FakeHttpClient({})  # listings.json, ATS, GitHub and HN all return 404
    run = run_pipeline(ctx, "2026-11-18")
    assert run.steps["ingest"] == "failed: HttpError"
    assert run.steps["trends"] == "ok"  # trend sources are optional: counted, not fatal
    assert run.metrics["trends.trend_source_failures"] == 2
    assert run.steps["publish"] == "ok" and run.status == "partial"
    assert (tmp_path / "site" / "data" / "dashboard.json").exists()


def test_run_step_rejects_unknown_step(ctx: Context) -> None:
    try:
        run_step(ctx, "deploy", "2026-11-18")
    except ValueError as e:
        assert "unknown step" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_linkedin_draft_uses_only_stored_numbers(ctx: Context) -> None:
    run_pipeline(ctx, "2026-11-18")
    text = weekly_post(ctx.repos, "2026-11-18", ctx.taxonomy)
    assert "read 3 new US postings" in text
    assert weekly_post(ctx.repos, "2025-01-01", ctx.taxonomy).startswith("(no postings")


def test_ideas_keep_coming_on_consecutive_days(ctx: Context) -> None:
    """Regression: the same top gaps every day used to dedupe every idea away after a few days."""
    from jobpulse.pipeline.gaps import run_gaps

    run_pipeline(ctx, "2026-11-18")
    first = {s for i in ctx.repos.all_ideas() for s in i.target_skills}
    result = run_gaps(ctx, "2026-11-19")  # same demand data, one day later
    second = [i for i in ctx.repos.all_ideas() if i.date == "2026-11-19"]
    assert result.metrics["gaps_recently_covered"] == len(first)
    assert all(not set(i.target_skills) & first for i in second)


def test_gap_ranking_and_dedupe() -> None:
    demand = [
        {"skill": "Rust", "share_30d": 0.2, "count_30d": 4},
        {"skill": "Python", "share_30d": 0.9, "count_30d": 18},
        {"skill": "Scala", "share_30d": 0.3, "count_30d": 1},  # below the minimum count
        {"skill": "Kafka", "share_30d": 0.25, "count_30d": 5},
    ]
    gaps = rank_gaps(demand, {"Rust": 1.0}, evidenced={"Python"})
    assert [g["skill"] for g in gaps] == ["Rust", "Kafka"]  # 0.2 * 2 = 0.4 beats 0.25 * 1
    assert is_duplicate("Rust key-value store", ("Rust",), ["A Rust key value store"], [])
    assert not is_duplicate("Minimal Kafka service", ("Kafka",), ["Minimal Rust service"], [])


def test_emf_record_shape() -> None:
    m = Metrics()
    m.incr("llm_calls", 3)
    m.incr("listings_total", 100)  # not whitelisted: kept as a log property, not a billed metric
    rec = m.emf_record("extract", "2026-11-18")
    assert rec["llm_calls"] == 3 and rec["listings_total"] == 100
    directive = rec["_aws"]["CloudWatchMetrics"][0]
    assert directive["Metrics"] == [{"Name": "llm_calls", "Unit": "Count"}]
    assert directive["Dimensions"] == [[]]
    assert len(EMF_METRICS) <= 10  # stays inside the CloudWatch custom-metric free tier
    buf = io.StringIO()
    JsonLogger(stream=buf, run_date="d").info("hello", n=1)
    record = json.loads(buf.getvalue())
    assert {k: v for k, v in record.items() if k != "ts"} == {
        "level": "INFO",
        "event": "hello",
        "run_date": "d",
        "n": 1,
    }
