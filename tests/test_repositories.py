"""Repository behavior is tested against every local DocStore adapter (AWS one: test_aws.py)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from jobpulse.adapters.local import MemoryDocStore, SqliteDocStore
from jobpulse.models import Extraction, Posting, ProjectIdea, RunRecord
from jobpulse.ports import DocStore
from jobpulse.repositories import Repos

StoreFactory = Callable[[Path], DocStore]
FACTORIES: list[StoreFactory] = [lambda p: MemoryDocStore(), lambda p: SqliteDocStore(p / "s.sqlite")]


def posting(pid: str, first_seen: str, text: str = "Python") -> Posting:
    return Posting(
        id=pid,
        company="Acme",
        title="SWE Intern",
        url="https://x",
        locations=("Seattle, WA",),
        category="Software",
        date_posted=first_seen,
        first_seen=first_seen,
        jd_text=text,
    )


@pytest.fixture(params=FACTORIES, ids=["memory", "sqlite"])
def repos(request: pytest.FixtureRequest, tmp_path: Path) -> Repos:
    return Repos(request.param(tmp_path))


def test_record_posting_dedupes_across_days(repos: Repos) -> None:
    assert repos.record_posting(posting("a", "2026-11-17"))
    assert not repos.record_posting(posting("a", "2026-11-18"))  # seen yesterday -> not new today
    assert [p.id for p in repos.postings_for_day("2026-11-17")] == ["a"]
    assert repos.postings_for_day("2026-11-18") == []


def test_rerunning_same_day_rewrites_not_duplicates(repos: Repos) -> None:
    assert repos.record_posting(posting("a", "2026-11-18", "v1"))
    assert repos.record_posting(posting("a", "2026-11-18", "v2"))
    day = repos.postings_for_day("2026-11-18")
    assert len(day) == 1 and day[0].jd_text == "v2"


def test_extraction_cache_is_versioned(repos: Repos) -> None:
    ex = Extraction(("Python",), (), "x", (), (), method="qwen")
    repos.cache_extraction("h1", "qwen:v1", ex)
    assert repos.cached_extraction("h1", "qwen:v1") == ex
    assert repos.cached_extraction("h1", "qwen:v2") is None


def test_ideas_replace_per_date(repos: Repos) -> None:
    idea = ProjectIdea("t", "w", "s", ("Go",), ("m",), ("Go",), "2026-11-18", "fake")
    repos.put_ideas("2026-11-18", [idea, idea])
    repos.put_ideas("2026-11-18", [idea])
    assert len(repos.all_ideas()) == 1


def test_runs_and_demand_round_trip(repos: Repos) -> None:
    repos.put_run(RunRecord("2026-11-18", "succeeded", "t0", "t1", {"ingest": "ok"}, {"ingest.x": 2.0}))
    assert repos.runs()[0].metrics == {"ingest.x": 2.0}
    repos.put_demand("2026-11-18", 3, {"Python": 2})
    assert repos.demand_series() == {"2026-11-18": {"postings": 3, "counts": {"Python": 2}}}


def test_put_if_absent_and_query_prefix(tmp_path: Path) -> None:
    for store in (MemoryDocStore(), SqliteDocStore(tmp_path / "q.sqlite")):
        assert store.put_if_absent("P", "a#1", {"v": 1})
        assert not store.put_if_absent("P", "a#1", {"v": 2})
        store.put("P", "b#1", {"v": 3})
        assert [sk for sk, _ in store.query("P", "a#")] == ["a#1"]
        store.delete("P", "a#1")
        assert store.get("P", "a#1") is None
