# PROGRESS_LOG

Resume work from `git status` + this file. Newest entries at the bottom.

## Conventions
- Stage tags `stage-N` mark the commit where that stage's code landed. Code for several stages was
  written in one pass before being split into commits, so an intermediate commit may import a module
  committed a little later; the full suite is green at every tag from `stage-3` on.
- Real LLM calls are logged to `.jobpulse/llm_ledger.jsonl` (gitignored; a summary is committed in
  `docs/llm_budget.json` at the end). Budget: <= 1,200 calls; hard stop in code at 1,150.
- Local run state: `out/state.sqlite` (gitignored). Committed demo output: `docs/demo/`.

## Log
- 2026-11-18 - Stage 1: core domain (models, taxonomy with aliases, ports, single-table repositories,
  SQLite/in-memory adapters, fake + Qwen providers, call ledger), CLI + runner, fixtures. 1 real
  LLM call (smoke test).
- 2026-11-18 - Stage 2: ingest from SimplifyJobs listings + adapted ATS fetcher (jd-resume-tailor, MIT).
  Live backfill ingest for 2026-10-20..2026-11-18 (no LLM): 257 postings, 108 with full JD text,
  96 unsupported ATS, 51 fetch failures (mostly robots.txt disallow: Workday tenants, SmartRecruiters
  API). Audited constraint rules against real JDs; fixed 3 false-positive patterns (tests added).
- 2026-11-18 - Stage 3: 30 JDs exported to `eval/jds/` (deterministic sha256 order,
  JD >= 800 chars); labeled blind by an independent Claude agent into `eval/labels.json`
  (304 labels: 209 required, 95 nice-to-have) before any system output existed.
  Eval (`uv run jobpulse eval --runs 3`, qwen3.8-flash, 91 calls): LLM micro P/R/F1 =
  0.961/0.932/0.946 (std 0.005/0.010/0.004), keyword baseline 0.846/0.954/0.896; run-to-run
  Jaccard 0.935. Results: `eval/results.json`, raw predictions: `eval/predictions/`.
- 2026-11-18 - Stages 4-7: market/trends, truthful tailoring (property test: 2,000 variants, 0
  violations), gaps + ideas, dashboard + LinkedIn draft. Integration tests cover idempotent re-runs
  and partial failure.
- 2026-11-18 - Stage 8: CDK stack + AWS adapters + Lambda handlers; `cdk synth` passes cdk-nag
  (0 unsuppressed; suppressions documented in infra/stack.py). Bundle smoke-tested in the official
  Lambda python3.12 arm64 image (colima). NOT deployed (parent session deploys after review).
- 2026-11-18 - Stage 9: EMF limited to 8 whitelisted metrics (CloudWatch custom-metric free tier),
  `jobpulse runs` history command, source-outage test (ingest fails -> run "partial", dashboard still
  published).
