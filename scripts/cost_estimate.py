"""Compute the monthly AWS cost ESTIMATE in docs/COST.md from measured run volumes.

Inputs (all produced by real runs in this repo):
  docs/measurements/run_ops.json        storage ops (DynamoDB RUs, S3 PUTs) of one replayed daily run
  docs/measurements/step_durations.json per-step wall time of the real 30-date backfill (we bill the MAX)
  docs/measurements/demo_summary.json   site size after the 30-date demo

Pricing constants below are public on-demand list prices (us-east-2) as known to the author at the time
of writing - they are ASSUMPTIONS and must be checked against https://aws.amazon.com/pricing before use.

    uv run python scripts/cost_estimate.py > docs/measurements/cost_estimate.json
"""

from __future__ import annotations

import json
from pathlib import Path

DAYS = 30
PRICE = {
    "lambda_gb_s_arm": 0.0000133334,
    "lambda_req": 0.20 / 1e6,
    "sfn_transition": 0.025 / 1000,
    "ddb_wru": 0.625 / 1e6,
    "ddb_rru": 0.125 / 1e6,
    "ddb_gb_month": 0.25,
    "s3_put": 0.005 / 1000,
    "s3_get": 0.0004 / 1000,
    "s3_gb_month": 0.023,
    "logs_ingest_gb": 0.50,
}
FREE = {  # monthly always-free allowances that apply to this workload
    "lambda_gb_s": 400_000,
    "lambda_req": 1_000_000,
    "sfn_transition": 4_000,
    "ddb_gb": 25,
    "logs_gb": 5,
}
# Lambda runs on a fraction of a vCPU at 256-512 MB. We bill the WORST observed day for every step,
# times SLOWDOWN, every day - a deliberately pessimistic assumption (most of the time is network/LLM wait).
SLOWDOWN = 3.0
MEMORY_GB = {"ingest": 0.25, "extract": 0.25, "trends": 0.25, "tailor": 0.5, "gaps": 0.25, "publish": 0.25}
TRANSITIONS_PER_RUN = 9  # start + 6 tasks + choice + succeed (catch paths add none on success)


def main() -> None:
    ops = json.loads(Path("docs/measurements/run_ops.json").read_text())
    demo = json.loads(Path("docs/measurements/demo_summary.json").read_text())
    durations = json.loads(Path("docs/measurements/step_durations.json").read_text())["steps"]

    gb_s_run = sum(durations[s]["max_s"] * SLOWDOWN * MEMORY_GB[s] for s in MEMORY_GB)
    gb_s = gb_s_run * DAYS
    lambda_reqs = 6 * DAYS
    lambda_cost = (
        max(0.0, gb_s - FREE["lambda_gb_s"]) * PRICE["lambda_gb_s_arm"]
        + max(0, lambda_reqs - FREE["lambda_req"]) * PRICE["lambda_req"]
    )
    transitions = TRANSITIONS_PER_RUN * DAYS
    sfn_cost = max(0, transitions - FREE["sfn_transition"]) * PRICE["sfn_transition"]
    t = ops["totals"]
    ddb_cost = t["wru"] * DAYS * PRICE["ddb_wru"] + t["rru"] * DAYS * PRICE["ddb_rru"]
    s3_puts = t["s3_puts"] * DAYS
    s3_gb = demo["site_bytes"] / 1e9
    s3_cost = s3_puts * PRICE["s3_put"] + s3_gb * PRICE["s3_gb_month"]
    rows = {
        "Lambda (arm64)": {
            "volume": f"{lambda_reqs} invocations, {gb_s:,.0f} GB-s",
            "usd": lambda_cost,
            "free_tier_covers": gb_s <= FREE["lambda_gb_s"],
        },
        "Step Functions (Standard)": {
            "volume": f"{transitions} state transitions",
            "usd": sfn_cost,
            "free_tier_covers": transitions <= FREE["sfn_transition"],
        },
        "DynamoDB (on-demand)": {
            "volume": f"{t['wru'] * DAYS:,.0f} WRU, {t['rru'] * DAYS:,.0f} RRU, "
            f"{ops['state_db_bytes'] / 1e6:.1f} MB data",
            "usd": ddb_cost,
            "free_tier_covers": False,
        },
        "S3": {
            "volume": f"{s3_puts:,} PUTs, {s3_gb * 1000:.1f} MB stored",
            "usd": s3_cost,
            "free_tier_covers": False,
        },
    }
    total = sum(r["usd"] for r in rows.values())
    print(
        json.dumps(
            {
                "assumptions": {
                    "days": DAYS,
                    "slowdown_vs_local": SLOWDOWN,
                    "prices": PRICE,
                    "free_tier": FREE,
                    "transitions_per_run": TRANSITIONS_PER_RUN,
                },
                "rows": {k: {**v, "usd": round(v["usd"], 4)} for k, v in rows.items()},
                "total_usd_month_estimate": round(total, 4),
                "gb_s_per_run": round(gb_s_run, 1),
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
