"""AWS Lambda handlers: thin adapters over the shared pipeline core.

Each Step Functions task invokes one handler with the state input, e.g.
    {"scheduled_time": "2026-11-18T14:00:00Z"}   (from EventBridge Scheduler)
    {"date": "2026-11-18"}                        (manual run / backfill)
and the handler only (1) resolves the run date, (2) builds the AWS Context once
per container, and (3) calls the same `run_step` the local CLI uses.

Configuration comes from environment variables set by the CDK stack:
    TABLE_NAME, SITE_BUCKET, LLM_PROVIDER (qwen|fake|none), DASHSCOPE_KEY_PARAM, QWEN_MODEL
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from jobpulse.adapters.aws import DynamoDocStore, S3BlobStore, read_secure_parameter
from jobpulse.adapters.http import UrllibHttpClient
from jobpulse.adapters.llm import CallLedger, FakeProvider, QwenProvider
from jobpulse.context import Context, Settings, demo_bank_toml
from jobpulse.observability import JsonLogger
from jobpulse.ports import LLMProvider
from jobpulse.repositories import Repos
from jobpulse.runner import run_step
from jobpulse.taxonomy import default_taxonomy

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Only these steps call the LLM. The CDK stack grants ssm:GetParameter on the API key to these
# functions alone (least privilege), so the other steps must never try to read it.
LLM_STEPS = frozenset({"extract", "gaps"})
_ctx: Context | None = None  # reused across warm invocations (and across steps in one container)


def resolve_date(event: dict[str, Any]) -> str:
    raw = (
        event.get("date") or str(event.get("scheduled_time", ""))[:10] or datetime.now(UTC).date().isoformat()
    )
    if not _DATE.match(raw):
        raise ValueError(f"bad run date: {raw!r}")
    return str(raw)


def _llm_from_env() -> LLMProvider | None:
    provider = os.environ.get("LLM_PROVIDER", "qwen")
    if provider == "none":
        return None
    if provider == "fake":
        return FakeProvider()
    key = read_secure_parameter(os.environ["DASHSCOPE_KEY_PARAM"])
    return QwenProvider(
        key,
        model=os.environ.get("QWEN_MODEL", "qwen3.8-flash"),
        ledger=CallLedger(None, max_calls=int(os.environ.get("MAX_LLM_CALLS", "200"))),
    )


def aws_context(step: str) -> Context:
    global _ctx
    if _ctx is None:
        _ctx = Context(
            repos=Repos(DynamoDocStore(os.environ["TABLE_NAME"])),
            site=S3BlobStore(os.environ["SITE_BUCKET"]),
            llm=_llm_from_env() if step in LLM_STEPS else None,
            http=UrllibHttpClient(),
            taxonomy=default_taxonomy(),
            bank_toml=demo_bank_toml(),  # public deployment shows the demo persona only
            log=JsonLogger(service="jobpulse"),
            settings=Settings(emf_metrics=True, github_token=os.environ.get("GITHUB_TOKEN") or None),
        )
    return _ctx


def _handle(step: str, event: dict[str, Any]) -> dict[str, Any]:
    run_date = resolve_date(event)
    ctx = aws_context(step)
    ledger = getattr(ctx.llm, "ledger", None)
    if isinstance(ledger, CallLedger):
        ledger.session_calls = 0  # MAX_LLM_CALLS is a per-invocation safety cap, not per container
    result = run_step(ctx, step, run_date)
    return {"date": run_date, **result.to_dict()}


def ingest(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    return _handle("ingest", event)


def extract(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    return _handle("extract", event)


def trends(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    return _handle("trends", event)


def tailor(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    return _handle("tailor", event)


def gaps(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    return _handle("gaps", event)


def publish(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    return _handle("publish", event)
