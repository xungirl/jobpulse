from __future__ import annotations

import json

import pytest

from jobpulse.adapters.llm import FakeProvider, LLMError
from jobpulse.adapters.local import MemoryDocStore
from jobpulse.context import Context
from jobpulse.pipeline.extract import (
    SchemaError,
    keyword_extract,
    llm_extract,
    parse_llm_json,
    run_extract,
    validate_extraction,
)
from jobpulse.pipeline.ingest import run_ingest
from jobpulse.taxonomy import Taxonomy

GOOD = json.dumps(
    {
        "required_skills": ["python", "k8s", "Quantum Widgets"],
        "nice_to_have": ["PyTorch", "Python"],
        "domain": "robotics",
        "seniority_signals": ["intern"],
        "constraints": [],
    }
)


def test_parse_tolerates_fences_and_prose() -> None:
    assert parse_llm_json('Sure!\n```json\n{"a": 1}\n```')["a"] == 1
    with pytest.raises(SchemaError):
        parse_llm_json("no json here")
    with pytest.raises(SchemaError):
        parse_llm_json("[1, 2]")


def test_validate_normalizes_onto_taxonomy(tax: Taxonomy) -> None:
    ex = validate_extraction(json.loads(GOOD), tax, "qwen")
    assert ex.required_skills == ("Python", "Kubernetes")
    assert ex.nice_to_have == ("PyTorch",)  # duplicate of a required skill removed
    assert ex.unmapped_skills == ("Quantum Widgets",)


@pytest.mark.parametrize(
    "bad",
    [
        {"nice_to_have": []},
        {"required_skills": "Python", "nice_to_have": []},
        {"required_skills": [1], "nice_to_have": []},
    ],
)
def test_validate_rejects_bad_shapes(tax: Taxonomy, bad: dict[str, object]) -> None:
    with pytest.raises(SchemaError):
        validate_extraction(bad, tax, "qwen")


def test_retry_after_invalid_reply(tax: Taxonomy) -> None:
    replies = iter(["not json", GOOD])
    llm = FakeProvider(lambda s, u, p: next(replies))
    ex = llm_extract(llm, tax, "SWE Intern", "Python")
    assert ex.required_skills == ("Python", "Kubernetes")
    assert llm.calls == ["extract:a0", "extract:a1"]


def test_raises_after_max_attempts(tax: Taxonomy) -> None:
    llm = FakeProvider(lambda s, u, p: "garbage")
    with pytest.raises(SchemaError):
        llm_extract(llm, tax, "SWE Intern", "Python")


def test_keyword_extract_splits_on_preferred_heading(tax: Taxonomy) -> None:
    ex = keyword_extract("Requirements\n- Python, SQL\nPreferred qualifications\n- Kafka, Python", tax)
    assert ex.required_skills == ("Python", "SQL")
    assert ex.nice_to_have == ("Kafka",)


def test_run_extract_caches_by_content_and_falls_back(ctx: Context) -> None:
    run_ingest(ctx, "2026-11-18")
    first = run_extract(ctx, "2026-11-18")
    assert first.metrics["llm_extractions"] == 3 and first.metrics["cache_hits"] == 0
    second = run_extract(ctx, "2026-11-18")  # same JD text -> no new LLM calls
    assert second.metrics["cache_hits"] == 3 and second.metrics["llm_calls"] == 0

    def down(system: str, user: str, purpose: str) -> str:
        raise LLMError("service unavailable")

    ctx.llm = FakeProvider(down)  # different provider name stays "fake" -> cache still hits; clear it
    store = ctx.repos.store
    assert isinstance(store, MemoryDocStore)
    store.items = {k: v for k, v in store.items.items() if not k[0].startswith("EXTRACT#")}
    third = run_extract(ctx, "2026-11-18")
    assert third.metrics["fallback_keyword"] == 3 and third.metrics["extract_failures"] == 3
    for p in ctx.repos.postings_for_day("2026-11-18"):
        ex = ctx.repos.posting_extraction(p.id)
        if p.jd_text:
            assert ex is not None and ex.method == "keyword"
