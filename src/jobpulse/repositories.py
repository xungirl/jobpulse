"""Domain repositories on top of the DocStore port (single-table key design).

    pk                  sk                  document
    ------------------  ------------------  ------------------------------------
    POSTING#<id>        META                Posting (dedupe: conditional put)
    POSTING#<id>        EXTRACTION          Extraction used for this posting (any method)
    DAY#<date>          POSTING#<id>        {} - index of postings first seen that day
    EXTRACT#<jd_hash>   <prompt_version>    Extraction (LLM cache keyed by content)
    DEMAND              <date>              {"postings": n, "counts": {skill: n}}
    TRENDS              <date>              {"signals": [TrendSignal...]}
    RESUME#<date>       <posting_id>        tailored-resume metadata
    IDEA                <date>#<n>          ProjectIdea
    REPORT              <date>              gap / summary data for the dashboard
    RUN                 <date>              RunRecord

Every write is keyed by date or content, so re-running a date overwrites the
same items instead of duplicating them (idempotency by construction).
"""

from __future__ import annotations

from typing import Any

from jobpulse.models import Extraction, Posting, ProjectIdea, RunRecord, TrendSignal
from jobpulse.ports import DocStore


class Repos:
    def __init__(self, store: DocStore) -> None:
        self.store = store

    # ---------------------------------------------------------------- postings
    def record_posting(self, posting: Posting) -> bool:
        """Store a posting the first time it is seen. Returns True if it is new for this date.

        A posting already first-seen on the same date is rewritten (re-run of that date);
        a posting first seen on an earlier date is left alone and reported as not new.
        """
        pk = f"POSTING#{posting.id}"
        if self.store.put_if_absent(pk, "META", posting.to_dict()):
            self.store.put(f"DAY#{posting.first_seen}", pk, {})
            return True
        existing = self.store.get(pk, "META")
        if existing and existing["first_seen"] == posting.first_seen:
            self.store.put(pk, "META", posting.to_dict())
            self.store.put(f"DAY#{posting.first_seen}", pk, {})
            return True
        return False

    def is_known(self, posting_id: str) -> bool:
        return self.store.get(f"POSTING#{posting_id}", "META") is not None

    def posting(self, posting_id: str) -> Posting | None:
        doc = self.store.get(f"POSTING#{posting_id}", "META")
        return Posting.from_dict(doc) if doc else None

    def first_seen(self, posting_id: str) -> str | None:
        doc = self.store.get(f"POSTING#{posting_id}", "META")
        return str(doc["first_seen"]) if doc else None

    def postings_for_day(self, date: str) -> list[Posting]:
        out = []
        for sk, _ in self.store.query(f"DAY#{date}", "POSTING#"):
            doc = self.store.get(sk, "META")
            if doc:
                out.append(Posting.from_dict(doc))
        return out

    def put_posting_extraction(self, posting_id: str, extraction: Extraction) -> None:
        self.store.put(f"POSTING#{posting_id}", "EXTRACTION", extraction.to_dict())

    def posting_extraction(self, posting_id: str) -> Extraction | None:
        doc = self.store.get(f"POSTING#{posting_id}", "EXTRACTION")
        return Extraction.from_dict(doc) if doc else None

    # ---------------------------------------------------------------- extraction cache
    def cached_extraction(self, jd_hash: str, version: str) -> Extraction | None:
        doc = self.store.get(f"EXTRACT#{jd_hash}", version)
        return Extraction.from_dict(doc) if doc else None

    def cache_extraction(self, jd_hash: str, version: str, extraction: Extraction) -> None:
        self.store.put(f"EXTRACT#{jd_hash}", version, extraction.to_dict())

    # ---------------------------------------------------------------- demand time series
    def put_demand(self, date: str, postings: int, counts: dict[str, int]) -> None:
        self.store.put("DEMAND", date, {"postings": postings, "counts": counts})

    def demand_series(self) -> dict[str, dict[str, Any]]:
        return dict(self.store.query("DEMAND"))

    # ---------------------------------------------------------------- trends
    def put_trends(self, date: str, signals: list[TrendSignal], meta: dict[str, Any]) -> None:
        self.store.put("TRENDS", date, {"signals": [s.to_dict() for s in signals], "meta": meta})

    def trends(self, date: str) -> list[TrendSignal]:
        doc = self.store.get("TRENDS", date)
        return [TrendSignal.from_dict(s) for s in doc["signals"]] if doc else []

    def trends_meta(self, date: str) -> dict[str, Any]:
        doc = self.store.get("TRENDS", date)
        return dict(doc.get("meta", {})) if doc else {}

    # ---------------------------------------------------------------- resumes
    def put_resume(self, date: str, posting_id: str, meta: dict[str, Any]) -> None:
        self.store.put(f"RESUME#{date}", posting_id, meta)

    def resumes(self, date: str) -> list[dict[str, Any]]:
        return [doc for _, doc in self.store.query(f"RESUME#{date}")]

    def clear_resumes(self, date: str) -> None:
        for sk, _ in self.store.query(f"RESUME#{date}"):
            self.store.delete(f"RESUME#{date}", sk)

    # ---------------------------------------------------------------- project ideas
    def put_ideas(self, date: str, ideas: list[ProjectIdea]) -> None:
        for sk, _ in self.store.query("IDEA", f"{date}#"):
            self.store.delete("IDEA", sk)  # re-running a date replaces its ideas
        for i, idea in enumerate(ideas):
            self.store.put("IDEA", f"{date}#{i}", idea.to_dict())

    def all_ideas(self) -> list[ProjectIdea]:
        return [ProjectIdea.from_dict(d) for _, d in self.store.query("IDEA")]

    # ---------------------------------------------------------------- reports & runs
    def put_report(self, date: str, report: dict[str, Any]) -> None:
        self.store.put("REPORT", date, report)

    def report(self, date: str) -> dict[str, Any]:
        return self.store.get("REPORT", date) or {}

    def put_run(self, run: RunRecord) -> None:
        self.store.put("RUN", run.date, run.to_dict())

    def run(self, date: str) -> RunRecord | None:
        doc = self.store.get("RUN", date)
        return RunRecord.from_dict(doc) if doc else None

    def runs(self) -> list[RunRecord]:
        return [RunRecord.from_dict(d) for _, d in self.store.query("RUN")]
