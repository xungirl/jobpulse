"""Structured JSON logs and per-run metrics.

Locally metrics print as one JSON summary line; in Lambda the same counters are
emitted in CloudWatch Embedded Metric Format (EMF): a specially shaped JSON log
line that CloudWatch turns into metrics with no API calls and no agent.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from typing import Any, TextIO

NAMESPACE = "JobPulse"
# Each distinct metric name is a billed CloudWatch custom metric beyond the first 10, so only these
# run-health numbers are published as metrics.
EMF_METRICS = frozenset(
    {
        "postings_ingested",
        "llm_calls",
        "cache_hit_rate",
        "jd_fetch_failed",
        "extract_failures",
        "tailor_failures",
        "resumes_generated",
        "step_failed",
    }
)


class JsonLogger:
    def __init__(self, stream: TextIO | None = None, **context: Any) -> None:
        self.stream = stream
        self.context = context

    def bind(self, **extra: Any) -> JsonLogger:
        return JsonLogger(self.stream, **{**self.context, **extra})

    def _emit(self, level: str, event: str, fields: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": level,
            "event": event,
            **self.context,
            **fields,
        }
        print(json.dumps(record, default=str), file=self.stream or sys.stderr)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("INFO", event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit("WARNING", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("ERROR", event, fields)


class Metrics:
    """Counters for one pipeline step/run."""

    def __init__(self) -> None:
        self.counters: dict[str, float] = {}
        self._started = time.monotonic()

    def incr(self, name: str, value: float = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def set(self, name: str, value: float) -> None:
        self.counters[name] = value

    def merge(self, other: dict[str, float]) -> None:
        for k, v in other.items():
            self.incr(k, v)

    def elapsed_s(self) -> float:
        return round(time.monotonic() - self._started, 2)

    def emf_record(self, step: str, run_date: str) -> dict[str, Any]:
        """One EMF document. Only EMF_METRICS become CloudWatch metrics (no dimensions, so at most
        8 custom metrics - inside the 10-metric free tier); every counter stays in the log line as a
        property, queryable with Logs Insights at no extra cost.
        """
        declared = sorted(n for n in self.counters if n in EMF_METRICS)
        return {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [[]],
                        "Metrics": [{"Name": n, "Unit": "Count"} for n in declared],
                    }
                ],
            },
            "Step": step,
            "run_date": run_date,
            **{n: self.counters[n] for n in sorted(self.counters)},
        }

    def flush(self, step: str, run_date: str, emf: bool, stream: TextIO | None = None) -> None:
        out = stream or sys.stdout
        if emf:
            print(json.dumps(self.emf_record(step, run_date)), file=out)
        else:
            print(
                json.dumps({"event": "metrics", "step": step, "run_date": run_date, **self.counters}),
                file=out,
            )
