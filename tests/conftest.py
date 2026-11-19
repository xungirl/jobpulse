"""Shared fixtures: an offline Context wired to fake HTTP, fake LLM and in-memory storage."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from jobpulse.adapters.http import FakeHttpClient
from jobpulse.adapters.llm import FakeProvider
from jobpulse.adapters.local import FileBlobStore, MemoryDocStore
from jobpulse.context import Context, Settings, demo_bank_toml
from jobpulse.observability import JsonLogger
from jobpulse.repositories import Repos
from jobpulse.sources.simplify import LISTINGS_URL
from jobpulse.sources.trends import GITHUB_SEARCH, HN_SEARCH
from jobpulse.taxonomy import Taxonomy, default_taxonomy

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def fake_responses() -> dict[str, str]:
    return {
        LISTINGS_URL: fixture_text("listings.json"),
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1001": fixture_text(
            "greenhouse_acme_1001.json"
        ),
        "https://boards-api.greenhouse.io/v1/boards/gamma/jobs/2002": fixture_text(
            "greenhouse_gamma_2002.json"
        ),
        "https://boards-api.greenhouse.io/v1/boards/delta/jobs/3003": "{not json",  # a broken ATS response
        "https://api.lever.co/v0/postings/beta/11111111-2222-3333-4444-555555555555": fixture_text(
            "lever_beta.json"
        ),
        GITHUB_SEARCH + "*": fixture_text("github_search.json"),
        HN_SEARCH + "*": fixture_text("hn_search.json"),
    }


@pytest.fixture
def tax() -> Taxonomy:
    return default_taxonomy()


@pytest.fixture
def fake_http() -> FakeHttpClient:
    return FakeHttpClient(fake_responses())


@pytest.fixture
def ctx(tmp_path: Path, fake_http: FakeHttpClient) -> Context:
    return Context(
        repos=Repos(MemoryDocStore()),
        site=FileBlobStore(tmp_path / "site"),
        llm=FakeProvider(),
        http=fake_http,
        taxonomy=default_taxonomy(),
        bank_toml=demo_bank_toml(),
        log=JsonLogger(stream=io.StringIO()),
        settings=Settings(),
    )
