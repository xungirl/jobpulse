"""Everything a pipeline step needs, bundled once and passed explicitly.

The same `Context` type is built two ways: `local_context()` (SQLite, files,
JSONL ledger) and `jobpulse.aws_runtime.aws_context()` (DynamoDB, S3, SSM).
Steps only see the ports, so they cannot tell which one they got.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from jobpulse.adapters.http import UrllibHttpClient
from jobpulse.adapters.llm import CallLedger, FakeProvider, QwenProvider, read_env_file_value
from jobpulse.adapters.local import FileBlobStore, SqliteDocStore
from jobpulse.observability import JsonLogger
from jobpulse.ports import BlobStore, HttpClient, LLMProvider
from jobpulse.repositories import Repos
from jobpulse.taxonomy import Taxonomy, default_taxonomy

DEV_LLM_BUDGET = 1150  # hard stop below the 1,200-call development budget


@dataclass(frozen=True)
class Settings:
    max_postings_per_run: int = 60  # caps JD fetches + LLM calls per day
    max_resumes_per_run: int = 25
    lookback_days: int = 1  # ingest postings dated D-1..D that we have not seen yet
    ideas_per_day: int = 3
    emf_metrics: bool = False  # True in Lambda
    github_token: str | None = None


@dataclass
class Context:
    repos: Repos
    site: BlobStore  # public dashboard files (served by CloudFront in AWS)
    llm: LLMProvider | None  # None -> keyword extraction only
    http: HttpClient
    taxonomy: Taxonomy
    bank_toml: str
    log: JsonLogger
    settings: Settings = field(default_factory=Settings)


def demo_bank_toml() -> str:
    return resources.files("jobpulse.data").joinpath("demo_bank.toml").read_text("utf-8")


def load_local_config(path: Path = Path("jobpulse.local.toml")) -> dict[str, str]:
    """Optional, gitignored personal config: env_file (for the API key) and bank (real bullet bank)."""
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return {k: str(v) for k, v in tomllib.load(f).items()}


def build_llm(provider: str, env_file: str | None, ledger_path: Path | None) -> LLMProvider | None:
    if provider == "none":
        return None
    if provider == "fake":
        return FakeProvider()
    key = os.environ.get("DASHSCOPE_API_KEY") or (
        read_env_file_value(env_file, "DASHSCOPE_API_KEY") if env_file else None
    )
    if not key:
        raise SystemExit("No DashScope key: set DASHSCOPE_API_KEY or env_file in jobpulse.local.toml")
    model = os.environ.get("QWEN_MODEL", "qwen3.8-flash")
    return QwenProvider(key, model=model, ledger=CallLedger(ledger_path, DEV_LLM_BUDGET))


def local_context(
    out_dir: Path,
    provider: str = "qwen",
    bank_path: str | None = None,
    settings: Settings | None = None,
    http: HttpClient | None = None,
    state_dir: Path = Path(".jobpulse"),
) -> Context:
    cfg = load_local_config()
    bank_file = bank_path or cfg.get("bank")
    bank = Path(bank_file).expanduser().read_text("utf-8") if bank_file else demo_bank_toml()
    return Context(
        repos=Repos(SqliteDocStore(out_dir / "state.sqlite")),
        site=FileBlobStore(out_dir / "site"),
        llm=build_llm(provider, cfg.get("env_file"), state_dir / "llm_ledger.jsonl"),
        http=http or UrllibHttpClient(),
        taxonomy=default_taxonomy(),
        bank_toml=bank,
        log=JsonLogger(),
        settings=settings or Settings(github_token=os.environ.get("GITHUB_TOKEN")),
    )
