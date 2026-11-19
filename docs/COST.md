# AWS cost: ESTIMATE (not yet deployed)

> **This is an estimate, not a bill.** jobpulse has not been deployed yet. The *volumes* below come from
> real local runs (sources linked). The *prices* are public on-demand list prices for us-east-2 as known
> to the author when writing; **verify them on https://aws.amazon.com/pricing before relying on them.**
> After deploying, replace this page with Cost Explorer numbers.

## Bottom line

| Scenario | Estimated monthly AWS cost |
|---|---|
| Within the AWS always-free allowances (typical for a personal account) | **≈ $0.01** (DynamoDB requests + S3 PUTs/storage) |
| With *no* free tier at all (worst case) | **< $0.10** (dominated by Lambda GB-seconds; see table) |

Guardrails defined in CDK: AWS Budgets e-mail alerts when the monthly bill exceeds **$1** and **$5**
(actual and forecast), plus a CloudWatch alarm and SNS e-mail on failed or degraded runs. The budgets
watch the whole account, not just this stack, which is deliberately conservative.

## Measured volumes (inputs)

| Input | Value | Source |
|---|---|---|
| Runs per month | 30 (one per day) | schedule `cron(0 14 * * ? *)` |
| Lambda invocations per run | 6 (one per step) | `infra/stack.py` |
| Worst observed wall time per step (local, M2) | ingest 50.7 s, extract 65.7 s, trends 15.2 s, tailor 0.6 s, gaps 71.5 s, publish 0.06 s | `docs/measurements/step_durations.json` |
| DynamoDB request units per run (heaviest demo day, 17 JDs, 12 resumes) | 118 WRU, 242 RRU | `docs/measurements/run_ops.json` |
| S3 PUTs per run (same day) | 19 PUTs, 236 KB | `docs/measurements/run_ops.json` |
| Data after 30 days | DynamoDB ≈ 2.1 MB (SQLite proxy), site ≈ 2.4 MB | `run_ops.json`, `demo_summary.json` |
| Structured logs | ≈ 81 KB for all 30 runs | backfill log size |
| Dashboard page weight | ≈ 87 KB (HTML + JS + CSS + data JSON) | `out/site` |

## Per-service estimate (30 runs per month)

| Service | Volume per month | Pricing assumption (us-east-2) | Estimate |
|---|---|---|---|
| Lambda (arm64) | 180 invocations; **4,596 GB-s** (= worst day × **3** slowdown × 30, at 256 MB, or 512 MB for tailor) | $0.0000133334 per GB-s, $0.20 per 1M requests; always-free 400,000 GB-s + 1M requests | **$0** free tier · $0.061 without |
| Step Functions (Standard) | ≈ 270 state transitions (8 states per run plus headroom) | $0.025 per 1,000 transitions; always-free 4,000 | **$0** free tier · $0.007 without |
| DynamoDB (on-demand) | 3,540 WRU, 7,260 RRU; ≈ 2 MB stored | $0.625 per 1M WRU, $0.125 per 1M RRU; storage $0.25/GB-month (25 GB always free) | **$0.003** |
| S3 Standard | 570 PUTs, ≈ 2.4 MB stored (lifecycle: PDFs 30 d, history 90 d) | PUT $0.005 per 1,000, storage $0.023/GB-month | **$0.003** |
| CloudFront | ≈ 0.09 MB per dashboard view; 1,000 views ≈ 0.09 GB | always-free 1 TB transfer + 10M requests/month | **$0** |
| EventBridge Scheduler | 30 invocations | always-free 14M/month | **$0** |
| CloudWatch Logs | well under 1 MB ingested (14-day retention) | $0.50/GB ingest; 5 GB always free | **$0** |
| CloudWatch metrics (EMF) | 8 custom metrics (deliberately capped, no dimensions) | first 10 custom metrics free, then $0.30 per metric-month | **$0** |
| CloudWatch alarm | 1 standard alarm | first 10 free | **$0** |
| X-Ray (state machine) | ≈ 30 traces | 100,000 traces/month free | **$0** |
| SSM Parameter Store | 1 standard SecureString (AWS-managed `aws/ssm` key) | standard parameters and standard throughput free | **$0** |
| SNS | a few e-mails | 1,000 e-mail notifications/month free | **$0** |
| AWS Budgets | 2 budgets | first 2 budgets free (assumption: verify) | **$0** |
| **Total** | | | **≈ $0.006 / month** (scripted) |

The total is computed by `scripts/cost_estimate.py` → `docs/measurements/cost_estimate.json`
(DynamoDB + S3 only, because everything else falls inside the always-free tier at this volume).

## What would make it expensive (and why it doesn't)

| Risk | Typical cost | What jobpulse does instead |
|---|---|---|
| NAT gateway (Lambdas in a VPC) | ≈ $32/month + data | no VPC; all calls go to public endpoints |
| Always-on compute (EC2/RDS/Fargate) | $10–30+/month | Lambdas run about 39 s per day on average (under 4 min on the worst observed day, summed over steps) |
| Per-step metric dimensions | ≈ 45 metrics × $0.30 ≈ $10/month | 8 dimensionless EMF metrics; the rest stays in logs |
| WAF on CloudFront | ≥ $5/month | static, public, read-only content; documented cdk-nag suppression |
| Customer-managed KMS keys | $1/month each | AWS-managed keys (SSE-S3, `aws/ssm`) |
| CloudFront invalidations | after 1,000/month | `Cache-Control` headers (60 s for data, 1 day for PDFs) |
| Runaway LLM loop | DashScope bill | content-hash cache, 60 postings/day cap, `MAX_LLM_CALLS=200` per invocation |

## LLM (DashScope) usage: not an AWS cost

Not priced here (DashScope pricing is outside AWS and changes). Measured token volumes from the
development ledger (`.jobpulse/llm_ledger.jsonl`, summarized in `docs/measurements/demo_summary.json`):

- Extraction: **217 successful calls**, mean **1,613 prompt + 204 completion tokens** per call, median
  latency 3.5 s.
- Project ideas: **93 calls**, mean **643 prompt + 622 completion tokens**.
- Daily volume in the demo: about 4.2 JDs per day with full text, so about 4 extraction calls + 1 ideas
  call per day. Re-runs cost 0 calls (cache: 17/17 hits, `docs/measurements/cache_rerun.json`).
