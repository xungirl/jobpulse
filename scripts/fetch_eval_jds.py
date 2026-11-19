"""Rebuild the local JD-text cache for the extraction eval.

The repo does not redistribute job-description text: each eval/jds/*.json keeps only the
posting URL, company, title and the sha256 + length of the exact text that was labeled.
This script re-fetches every URL through the same public-ATS fetcher the pipeline uses and
writes eval/.jd_cache/<id>.txt (gitignored).

    uv run python scripts/fetch_eval_jds.py

A fetched text is only cached when its sha256 matches the labeled one, so the eval always
runs on byte-identical inputs. Postings that have closed or changed since labeling are
reported and skipped; eval/results.json remains the record of the published run.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from jobpulse.adapters.http import UrllibHttpClient
from jobpulse.ports import HttpError
from jobpulse.sources.ats import fetch_job_text

EVAL = Path(__file__).resolve().parent.parent / "eval"


def main() -> int:
    cache = EVAL / ".jd_cache"
    cache.mkdir(exist_ok=True)
    http = UrllibHttpClient()
    ok = changed = gone = 0
    for path in sorted((EVAL / "jds").glob("*.json")):
        meta = json.loads(path.read_text())
        target = cache / f"{path.stem}.txt"
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == meta["jd_sha256"]:
            ok += 1
            continue
        try:
            job = fetch_job_text(http, meta["url"])
        except HttpError as e:
            print(f"{path.stem}: fetch failed ({e})", file=sys.stderr)
            gone += 1
            continue
        if job is None or not job.text:
            print(f"{path.stem}: posting closed or unsupported", file=sys.stderr)
            gone += 1
            continue
        if hashlib.sha256(job.text.encode()).hexdigest() != meta["jd_sha256"]:
            print(f"{path.stem}: text changed since labeling; not cached", file=sys.stderr)
            changed += 1
            continue
        target.write_text(job.text)
        ok += 1
    print(f"cached {ok}/{ok + changed + gone} (changed {changed}, unavailable {gone})")
    return 0 if ok == ok + changed + gone else 1


if __name__ == "__main__":
    raise SystemExit(main())
