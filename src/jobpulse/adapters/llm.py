"""LLM providers: Qwen (DashScope, OpenAI-compatible HTTP) and a deterministic fake.

The Qwen client uses only urllib so the Lambda bundle stays small. Every real
call is appended to a JSONL ledger (purpose, latency, tokens — never the key or
the prompt), and a hard budget stops runaway spending during development.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from jobpulse.taxonomy import default_taxonomy

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


class CallLedger:
    """Counts real LLM calls. Local: JSONL file. In Lambda: in-memory (metrics carry the count)."""

    def __init__(self, path: Path | None, max_calls: int | None) -> None:
        self.path = path
        self.max_calls = max_calls
        self.session_calls = 0

    def total_calls(self) -> int:
        if self.path is None or not self.path.exists():
            return self.session_calls
        with self.path.open() as f:
            return sum(1 for _ in f)

    def check(self) -> None:
        if self.max_calls is not None and self.total_calls() >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget of {self.max_calls} reached")

    def record(self, entry: dict[str, object]) -> None:
        self.session_calls += 1
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")


class QwenProvider:
    name = "qwen"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3.8-flash",
        base_url: str = DASHSCOPE_BASE_URL,
        temperature: float = 0.2,
        timeout_s: float = 60.0,
        ledger: CallLedger | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("empty DashScope API key")
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.ledger = ledger or CallLedger(None, None)

    def __repr__(self) -> str:  # never leak the key through logs / tracebacks
        return f"QwenProvider(model={self.model!r})"

    def complete_json(self, system: str, user: str, purpose: str) -> str:
        self.ledger.check()
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        entry: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "purpose": purpose,
            "model": self.model,
        }
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                payload = json.loads(r.read().decode("utf-8"))
            usage = payload.get("usage") or {}
            entry.update(ok=True, prompt_tokens=usage.get("prompt_tokens"),
                         completion_tokens=usage.get("completion_tokens"))
            return str(payload["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            entry.update(ok=False, error=f"HTTP {e.code}")
            raise LLMError(f"DashScope HTTP {e.code}") from None  # `from None`: drop request (has key)
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
            entry.update(ok=False, error=type(e).__name__)
            raise LLMError(f"DashScope call failed: {type(e).__name__}") from None
        finally:
            entry["latency_s"] = round(time.monotonic() - started, 2)
            self.ledger.record(entry)


Responder = Callable[[str, str, str], str]


def _fake_extract(user: str) -> str:
    """Pretend-LLM extraction: keyword matching over the JD, split by section headings."""
    tax = default_taxonomy()
    jd = user.split("<jd>", 1)[-1].split("</jd>", 1)[0]
    nice_idx = re.search(r"(preferred|nice to have|bonus|plus)", jd, re.IGNORECASE)
    head, tail = (jd[: nice_idx.start()], jd[nice_idx.start():]) if nice_idx else (jd, "")
    required = sorted(tax.find(head))
    nice = sorted(tax.find(tail) - set(required))
    return json.dumps({
        "required_skills": required,
        "nice_to_have": nice,
        "domain": "software",
        "seniority_signals": ["intern"],
        "constraints": [],
    })


def _fake_ideas(user: str) -> str:
    gaps = re.findall(r"^- (.+?) \(demand", user, flags=re.MULTILINE)[:3] or ["Testing"]
    ideas = [
        {
            "title": f"{g} starter service",
            "why": f"{g} appears in the demand table supplied in the prompt.",
            "scope": f"A small, tested service that exercises {g} end to end.",
            "stack": [g, "Python"],
            "success_metrics": ["test suite passes in CI", "README documents one measured result"],
            "target_skills": [g],
        }
        for g in gaps
    ]
    return json.dumps({"ideas": ideas})


def default_fake_responder(system: str, user: str, purpose: str) -> str:
    if purpose.startswith("extract"):
        return _fake_extract(user)
    if purpose.startswith("ideas"):
        return _fake_ideas(user)
    return "{}"


class FakeProvider:
    """Deterministic provider for tests/CI. `responder` can be swapped to simulate failures."""

    name = "fake"

    def __init__(self, responder: Responder = default_fake_responder) -> None:
        self.responder = responder
        self.calls: list[str] = []

    def complete_json(self, system: str, user: str, purpose: str) -> str:
        self.calls.append(purpose)
        return self.responder(system, user, purpose)


def read_env_file_value(path: str | Path, key: str) -> str | None:
    """Read KEY=value from a dotenv-style file without exporting it to the environment."""
    p = Path(path).expanduser()
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip('"').strip("'") or None
    return None
