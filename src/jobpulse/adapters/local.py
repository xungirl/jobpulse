"""Local adapters: SQLite document store and filesystem blob store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SqliteDocStore:
    """DocStore on one SQLite table with a composite primary key (pk, sk)."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS docs (pk TEXT NOT NULL, sk TEXT NOT NULL, doc TEXT NOT NULL,"
            " PRIMARY KEY (pk, sk))"
        )
        self._db.commit()

    def put(self, pk: str, sk: str, doc: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO docs (pk, sk, doc) VALUES (?, ?, ?)", (pk, sk, json.dumps(doc))
        )
        self._db.commit()

    def put_if_absent(self, pk: str, sk: str, doc: dict[str, Any]) -> bool:
        cur = self._db.execute(
            "INSERT OR IGNORE INTO docs (pk, sk, doc) VALUES (?, ?, ?)", (pk, sk, json.dumps(doc))
        )
        self._db.commit()
        return cur.rowcount == 1

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT doc FROM docs WHERE pk = ? AND sk = ?", (pk, sk)).fetchone()
        return json.loads(row[0]) if row else None

    def query(self, pk: str, sk_prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
        rows = self._db.execute(
            "SELECT sk, doc FROM docs WHERE pk = ? AND substr(sk, 1, ?) = ? ORDER BY sk",
            (pk, len(sk_prefix), sk_prefix),
        ).fetchall()
        return [(sk, json.loads(doc)) for sk, doc in rows]

    def delete(self, pk: str, sk: str) -> None:
        self._db.execute("DELETE FROM docs WHERE pk = ? AND sk = ?", (pk, sk))
        self._db.commit()


class FileBlobStore:
    """BlobStore writing under a root directory (content type / caching are S3-only concerns)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"blob key escapes root: {key}")
        return p

    def put_bytes(self, key: str, data: bytes, content_type: str, cache_seconds: int = 300) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.exists() else None

    def list_keys(self, prefix: str) -> list[str]:
        if not self.root.exists():
            return []
        keys = (str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))


class MemoryDocStore:
    """In-memory DocStore for unit tests."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put(self, pk: str, sk: str, doc: dict[str, Any]) -> None:
        self.items[(pk, sk)] = json.loads(json.dumps(doc))

    def put_if_absent(self, pk: str, sk: str, doc: dict[str, Any]) -> bool:
        if (pk, sk) in self.items:
            return False
        self.put(pk, sk, doc)
        return True

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        doc = self.items.get((pk, sk))
        return json.loads(json.dumps(doc)) if doc is not None else None

    def query(self, pk: str, sk_prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
        return sorted(
            (sk, json.loads(json.dumps(d)))
            for (p, sk), d in self.items.items()
            if p == pk and sk.startswith(sk_prefix)
        )

    def delete(self, pk: str, sk: str) -> None:
        self.items.pop((pk, sk), None)
