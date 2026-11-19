"""Least privilege: only LLM steps may read the API key from SSM.

Regression test for the first real deploy (2026-11-18), where every function read the key at
cold start but only extract/gaps were granted ssm:GetParameter, so ingest/trends/tailor/publish
failed with AccessDeniedException.
"""

from __future__ import annotations

import pytest

import jobpulse.lambdas as lam


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lam, "_ctx", None)
    monkeypatch.setattr(lam, "DynamoDocStore", lambda name: object())
    monkeypatch.setattr(lam, "S3BlobStore", lambda name: object())
    monkeypatch.setattr(lam, "Repos", lambda store: object())
    monkeypatch.setenv("TABLE_NAME", "t")
    monkeypatch.setenv("SITE_BUCKET", "b")
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_KEY_PARAM", "/jobpulse/dashscope-api-key")


@pytest.mark.parametrize("step", ["ingest", "trends", "tailor", "publish"])
def test_non_llm_steps_never_read_the_key(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    def forbidden(name: str) -> str:
        raise AssertionError(f"{step} must not read {name}")

    monkeypatch.setattr(lam, "read_secure_parameter", forbidden)
    ctx = lam.aws_context(step)
    assert ctx.llm is None


@pytest.mark.parametrize("step", sorted(lam.LLM_STEPS))
def test_llm_steps_read_the_key(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    reads: list[str] = []
    monkeypatch.setattr(lam, "read_secure_parameter", lambda name: reads.append(name) or "k")
    ctx = lam.aws_context(step)
    assert ctx.llm is not None
    assert reads == ["/jobpulse/dashscope-api-key"]


def test_stack_grants_match_llm_steps() -> None:
    from pathlib import Path

    stack = (Path(__file__).resolve().parent.parent / "infra" / "stack.py").read_text()
    assert 'if spec.name in ("extract", "gaps"):' in stack
    assert {"extract", "gaps"} == lam.LLM_STEPS
