from __future__ import annotations

import pytest

from jobpulse.pipeline.market import demand_table, trend_signals, window_share
from jobpulse.sources.trends import AttentionItem
from jobpulse.taxonomy import Taxonomy


def day(postings: int, **counts: int) -> dict[str, object]:
    return {"postings": postings, "counts": counts}


def test_window_share_sums_within_window() -> None:
    series = {"2026-11-10": day(10, Python=5), "2026-11-17": day(10, Python=2), "2026-11-18": day(10, Python=8)}
    n, share = window_share(series, "2026-11-18", 7)
    assert n == 20 and share["Python"] == pytest.approx(0.5)


def test_demand_deltas_in_share_points(tax: Taxonomy) -> None:
    series = {"2026-11-05": day(10, Python=2), "2026-11-12": day(10, Python=6)}
    row = next(r for r in demand_table(series, "2026-11-12", tax) if r["skill"] == "Python")
    assert row["share_7d"] == pytest.approx(0.6)
    assert row["delta_7d"] == pytest.approx(0.4)  # 60% this week vs 20% the week before
    assert row["delta_30d"] is None  # no previous 30-day window yet -> unknown, not zero


def test_trend_signals_map_and_normalize(tax: Taxonomy) -> None:
    items = [
        AttentionItem("github", "agent kit LLM agents in Rust", 900, ""),
        AttentionItem("github", "kubernetes operator Go", 300, ""),
        AttentionItem("hn", "A Rust database", 400, ""),
    ]
    signals = {s.skill: s for s in trend_signals(items, tax)}
    assert signals["Rust"].github_repos == 1 and signals["Rust"].hn_stories == 1
    assert signals["Rust"].score == pytest.approx(1.0)
    assert 0 < signals["Kubernetes"].score < 1
