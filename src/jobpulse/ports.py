"""Ports: the only interfaces business logic depends on.

Adapters implement them twice: locally (SQLite + filesystem + urllib) and on
AWS (DynamoDB + S3). Pipeline code never imports boto3 or sqlite3.

The storage port is deliberately narrow — a partition-key / sort-key document
store — because it maps 1:1 onto a DynamoDB single-table design and onto one
SQLite table. All repository logic is then written (and tested) once.
"""

from __future__ import annotations

from typing import Any, Protocol


class DocStore(Protocol):
    def put(self, pk: str, sk: str, doc: dict[str, Any]) -> None:
        """Create or overwrite one document."""

    def put_if_absent(self, pk: str, sk: str, doc: dict[str, Any]) -> bool:
        """Create a document only if (pk, sk) does not exist. Returns True if written."""

    def get(self, pk: str, sk: str) -> dict[str, Any] | None: ...

    def query(self, pk: str, sk_prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
        """All (sk, doc) under a partition key, sorted by sk, optionally filtered by prefix."""

    def delete(self, pk: str, sk: str) -> None: ...


class BlobStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str, cache_seconds: int = 300) -> None: ...

    def get_bytes(self, key: str) -> bytes | None: ...

    def list_keys(self, prefix: str) -> list[str]: ...


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, purpose: str) -> str:
        """Return the raw text of one chat completion that should contain a JSON object."""


class HttpClient(Protocol):
    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """GET a URL and return the decoded body. Raises HttpError on non-2xx / disallowed."""


class HttpError(Exception):
    def __init__(self, url: str, status: int | None, reason: str) -> None:
        super().__init__(f"{status} {reason}: {url}")
        self.url = url
        self.status = status
        self.reason = reason
