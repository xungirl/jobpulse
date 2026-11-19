"""Summarize the committed demo run (and the LLM call ledger) into docs/measurements/demo_summary.json.

    uv run python scripts/demo_summary.py --out-dir out
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from jobpulse.adapters.local import SqliteDocStore
from jobpulse.repositories import Repos


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--ledger", default=".jobpulse/llm_ledger.jsonl")
    a = ap.parse_args()
    repos = Repos(SqliteDocStore(Path(a.out_dir) / "state.sqlite"))
    runs = sorted(repos.runs(), key=lambda r: r.date)

    def total(key: str) -> float:
        return sum(r.metrics.get(key, 0) for r in runs)

    resumes = [m for r in runs for m in repos.resumes(r.date)]
    ideas = repos.all_ideas()
    site = Path(a.out_dir) / "site"
    ledger = [json.loads(line) for line in Path(a.ledger).read_text().splitlines()] if Path(a.ledger).exists() else []
    by_purpose = Counter(e["purpose"].split(":")[0] for e in ledger)
    ok = [e for e in ledger if e.get("ok")]
    summary = {
        "dates": [runs[0].date, runs[-1].date] if runs else [],
        "runs": len(runs),
        "run_status": dict(Counter(r.status for r in runs)),
        "postings_ingested": total("ingest.postings_ingested"),
        "postings_with_jd": total("ingest.postings_with_jd"),
        "jd_fetch_failed": total("ingest.jd_fetch_failed"),
        "jd_unsupported_ats": total("ingest.jd_unsupported_ats"),
        "jd_closed": total("ingest.jd_closed"),
        "llm_extractions": total("extract.llm_extractions"),
        "extract_cache_hits": total("extract.cache_hits"),
        "extract_fallback_keyword": total("extract.fallback_keyword"),
        "extract_llm_calls": total("extract.llm_calls"),
        "gaps_llm_calls": total("gaps.llm_calls"),
        "resumes_generated": len(resumes),
        "truthfulness_checks_passed": total("tailor.truthfulness_checks_passed"),
        "overflow_bullets_dropped": total("tailor.overflow_bullets_dropped"),
        "skipped_ineligible": total("tailor.skipped_ineligible"),
        "coverage_before_mean": round(sum(r["coverage_before"] for r in resumes) / len(resumes), 4) if resumes else None,
        "coverage_after_mean": round(sum(r["coverage_after"] for r in resumes) / len(resumes), 4) if resumes else None,
        "resumes_improved": sum(r["coverage_after"] > r["coverage_before"] for r in resumes),
        "resumes_unchanged": sum(r["coverage_after"] == r["coverage_before"] for r in resumes),
        "resumes_worse": sum(r["coverage_after"] < r["coverage_before"] for r in resumes),
        "ideas_total": len(ideas),
        "ideas_by_source": dict(Counter(i.source for i in ideas)),
        "avg_jd_postings_per_day": round(total("ingest.postings_with_jd") / len(runs), 2) if runs else 0,
        "site_bytes": sum(p.stat().st_size for p in site.rglob("*") if p.is_file()),
        "ledger": {
            "total_real_llm_calls": len(ledger),
            "by_purpose": dict(by_purpose),
            "failed_calls": len(ledger) - len(ok),
            "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in ok),
            "completion_tokens": sum(e.get("completion_tokens") or 0 for e in ok),
            "mean_latency_s": round(sum(e["latency_s"] for e in ledger) / len(ledger), 2) if ledger else None,
        },
    }
    out = Path("docs/measurements/demo_summary.json")
    out.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
