"""Command-line interface.

uv run jobpulse run --local --date 2026-11-18            # full pipeline, real LLM
uv run jobpulse run --local --date 2026-11-18 --llm fake # no API key needed
uv run jobpulse backfill --start 2026-10-20 --end 2026-11-18
uv run jobpulse linkedin --week-ending 2026-11-18
uv run jobpulse eval --runs 3                             # extraction precision/recall
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jobpulse.adapters.http import MemoizingHttpClient, UrllibHttpClient
from jobpulse.context import Settings, local_context
from jobpulse.runner import ORDER, run_pipeline
from jobpulse.sources.simplify import LISTINGS_URL


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _settings(a: argparse.Namespace) -> Settings:
    return Settings(max_postings_per_run=a.max_postings, max_resumes_per_run=a.max_resumes)


def cmd_run(a: argparse.Namespace) -> int:
    if not a.local:
        print("Only --local runs from the CLI; on AWS the Step Functions schedule runs the pipeline.")
        return 2
    ctx = local_context(Path(a.out), provider=a.llm, bank_path=a.bank, settings=_settings(a))
    run = run_pipeline(ctx, a.date, a.steps.split(",") if a.steps else None)
    print(json.dumps({"date": run.date, "status": run.status, "steps": run.steps}, indent=1))
    print(f"dashboard: {Path(a.out) / 'site' / 'index.html'}")
    return 0 if run.status == "succeeded" else 1


def cmd_backfill(a: argparse.Namespace) -> int:
    http = MemoizingHttpClient(UrllibHttpClient(), {LISTINGS_URL})  # one listings download per backfill
    ctx = local_context(Path(a.out), provider=a.llm, bank_path=a.bank, settings=_settings(a), http=http)
    d, end, failed = date.fromisoformat(a.start), date.fromisoformat(a.end), 0
    while d <= end:
        run = run_pipeline(ctx, d.isoformat())
        failed += run.status != "succeeded"
        print(f"{d} {run.status} {run.steps}", flush=True)
        d += timedelta(days=1)
    return 1 if failed else 0


def cmd_linkedin(a: argparse.Namespace) -> int:
    from jobpulse.pipeline.linkedin import weekly_post

    ctx = local_context(Path(a.out), provider="none", bank_path=a.bank)
    text = weekly_post(ctx.repos, a.week_ending, ctx.taxonomy)
    out = Path(a.out) / "linkedin" / f"{a.week_ending}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n")
    print(text)
    print(f"\n(draft saved to {out}; jobpulse never posts anything)", file=sys.stderr)
    return 0


def cmd_runs(a: argparse.Namespace) -> int:
    """Run history with the per-run health metrics (what the dashboard timeline shows)."""
    ctx = local_context(Path(a.out), provider="none", bank_path=a.bank)
    cols = [
        ("ingest.postings_ingested", "ingested"),
        ("extract.llm_calls", "llm"),
        ("extract.cache_hit_rate", "cache_hit"),
        ("extract.fallback_keyword", "fallback"),
        ("ingest.jd_fetch_failed", "fetch_fail"),
        ("tailor.resumes_generated", "resumes"),
    ]
    print(f"{'date':<11} {'status':<10} " + " ".join(f"{h:>10}" for _, h in cols))
    for run in sorted(ctx.repos.runs(), key=lambda r: r.date)[-a.last :]:
        vals = " ".join(f"{run.metrics.get(k, 0):>10g}" for k, _ in cols)
        print(f"{run.date:<11} {run.status:<10} {vals}")
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from jobpulse.evaluation import run_eval

    return run_eval(Path(a.dir), runs=a.runs, provider=a.llm, out_path=Path(a.dir) / "results.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="jobpulse", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--out", default="out", help="output dir for local state + site (default ./out)")
        p.add_argument("--llm", choices=["qwen", "fake", "none"], default="qwen")
        p.add_argument("--bank", help="bullet bank TOML (default: jobpulse.local.toml `bank`, else demo)")
        p.add_argument("--max-postings", type=int, default=60)
        p.add_argument("--max-resumes", type=int, default=25)

    r = sub.add_parser("run", help="run the pipeline for one date")
    r.add_argument("--local", action="store_true", help="local adapters (SQLite + ./out)")
    r.add_argument("--date", default=_today())
    r.add_argument("--steps", help=f"comma-separated subset of {','.join(ORDER)}")
    common(r)
    r.set_defaults(fn=cmd_run)

    b = sub.add_parser("backfill", help="run the pipeline for each date in a range, oldest first")
    b.add_argument("--start", required=True)
    b.add_argument("--end", default=_today())
    common(b)
    b.set_defaults(fn=cmd_backfill)

    li = sub.add_parser("linkedin", help="write a weekly LinkedIn post draft (text only)")
    li.add_argument("--week-ending", default=_today())
    li.add_argument("--out", default="out")
    li.add_argument("--bank")
    li.set_defaults(fn=cmd_linkedin)

    rs = sub.add_parser("runs", help="show recent run history and health metrics")
    rs.add_argument("--out", default="out")
    rs.add_argument("--bank")
    rs.add_argument("--last", type=int, default=30)
    rs.set_defaults(fn=cmd_runs)

    e = sub.add_parser("eval", help="extraction precision/recall vs hand labels")
    e.add_argument("--dir", default="eval")
    e.add_argument("--runs", type=int, default=3)
    e.add_argument("--llm", choices=["qwen", "fake"], default="qwen")
    e.set_defaults(fn=cmd_eval)

    a = ap.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
