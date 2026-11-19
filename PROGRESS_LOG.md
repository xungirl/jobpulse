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
- 2026-11-18 - Stage 3 (in progress): 30 JDs exported to `eval/jds/` (deterministic sha256 order,
  JD >= 800 chars); labeled blind by an independent Claude agent into `eval/labels.json`
  (304 labels: 209 required, 95 nice-to-have) before any system output existed.
