"""Measure what ONE daily run does to storage, as input for docs/COST.md.

Replays a date on a COPY of the local state (never touches ./out) with counting
wrappers around the DocStore and BlobStore, and converts item sizes into
DynamoDB request units (on-demand: 1 WRU per 1 KB written; 0.5 RRU per 4 KB
read, eventually consistent). LLM calls are served from the extraction cache
(a fake provider named "qwen" so cache keys match) - the real number of LLM
calls per day is taken from the run history instead.

    uv run python scripts/measure_run_ops.py --state out/state.sqlite --date 2026-11-18
"""

from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from jobpulse.adapters.http import MemoizingHttpClient, UrllibHttpClient
from jobpulse.adapters.llm import FakeProvider
from jobpulse.adapters.local import FileBlobStore, SqliteDocStore
from jobpulse.context import Context, Settings, demo_bank_toml
from jobpulse.observability import JsonLogger
from jobpulse.repositories import Repos
from jobpulse.runner import ORDER, run_step
from jobpulse.sources.simplify import LISTINGS_URL
from jobpulse.taxonomy import default_taxonomy


class CountingDocStore:
    def __init__(self, inner: SqliteDocStore) -> None:
        self.inner = inner
        self.writes = self.reads = 0
        self.wru = self.rru = 0.0

    def _w(self, doc: dict[str, Any], pk: str, sk: str) -> None:
        self.writes += 1
        self.wru += max(1, math.ceil((len(json.dumps(doc)) + len(pk) + len(sk) + 20) / 1024))

    def _r(self, nbytes: int) -> None:
        self.reads += 1
        self.rru += 0.5 * max(1, math.ceil(nbytes / 4096))

    def put(self, pk: str, sk: str, doc: dict[str, Any]) -> None:
        self._w(doc, pk, sk)
        self.inner.put(pk, sk, doc)

    def put_if_absent(self, pk: str, sk: str, doc: dict[str, Any]) -> bool:
        self._w(doc, pk, sk)
        return self.inner.put_if_absent(pk, sk, doc)

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        doc = self.inner.get(pk, sk)
        self._r(len(json.dumps(doc)) if doc else 0)
        return doc

    def query(self, pk: str, sk_prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
        rows = self.inner.query(pk, sk_prefix)
        self._r(sum(len(json.dumps(d)) for _, d in rows))
        return rows

    def delete(self, pk: str, sk: str) -> None:
        self.writes += 1
        self.wru += 1
        self.inner.delete(pk, sk)


class CountingBlobStore(FileBlobStore):
    puts = 0
    put_bytes_total = 0

    def put_bytes(self, key: str, data: bytes, content_type: str, cache_seconds: int = 300) -> None:
        CountingBlobStore.puts += 1
        CountingBlobStore.put_bytes_total += len(data)
        super().put_bytes(key, data, content_type, cache_seconds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="out/state.sqlite")
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", default="docs/measurements/run_ops.json")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp())
    shutil.copy(a.state, tmp / "state.sqlite")
    store = CountingDocStore(SqliteDocStore(tmp / "state.sqlite"))
    llm = FakeProvider()
    llm.name = "qwen"  # cache keys match the real run -> cache hits, zero real LLM calls
    ctx = Context(Repos(store), CountingBlobStore(tmp / "site"), llm,
                  MemoizingHttpClient(UrllibHttpClient(), {LISTINGS_URL}), default_taxonomy(),
                  demo_bank_toml(), JsonLogger(stream=io.StringIO()), Settings())
    steps: dict[str, dict[str, float]] = {}
    for step in ORDER:
        w0, r0, wru0, rru0, t0 = store.writes, store.reads, store.wru, store.rru, time.monotonic()
        try:
            run_step(ctx, step, a.date)
            status = "ok"
        except Exception as e:  # noqa: BLE001 - measurement only
            status = f"failed: {type(e).__name__}"
        steps[step] = {"status": status, "seconds_local": round(time.monotonic() - t0, 2),
                       "doc_writes": store.writes - w0, "doc_reads": store.reads - r0,
                       "wru": store.wru - wru0, "rru": store.rru - rru0}
    result = {
        "date": a.date,
        "note": "one replayed daily run on a copy of the demo state; seconds are Apple M2 wall time "
                "incl. network (ingest re-fetches listings.json)",
        "steps": steps,
        "totals": {"doc_writes": store.writes, "doc_reads": store.reads, "wru": store.wru, "rru": store.rru,
                   "s3_puts": CountingBlobStore.puts, "s3_put_bytes": CountingBlobStore.put_bytes_total},
        "state_db_bytes": (tmp / "state.sqlite").stat().st_size,
    }
    Path(a.out).write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
