"""Skill taxonomy: canonical names, aliases, and a keyword skill finder.

The taxonomy is the shared vocabulary of the whole system: LLM output is
normalized onto it, demand is counted on it, trends are mapped onto it, and
resume bullets are matched against it.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

# A short, case-sensitive token like "Go" or "R" only counts when it sits in a
# list-like context ("Python, Go", "C/C++", "(R)"); otherwise "Go beyond" would match.
_LIST_CONTEXT = re.compile(r"[,/()]|\band\b|\bor\b")


@dataclass(frozen=True)
class Skill:
    name: str
    category: str
    aliases: tuple[str, ...]
    exact: tuple[str, ...]


def _boundary_pattern(alias: str) -> str:
    # \b does not work for names like "C++", ".NET" or "C#", so define the
    # boundary ourselves: no word char (or + # .) directly before/after.
    return rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#]|\.\w)"


class Taxonomy:
    def __init__(self, skills: Iterable[Skill]) -> None:
        self.skills: dict[str, Skill] = {s.name: s for s in skills}
        self._lookup: dict[str, str] = {}
        for s in self.skills.values():
            if s.name not in s.exact:
                self._lookup[s.name.lower()] = s.name
            for a in s.aliases:
                self._lookup[a.lower()] = s.name
        insensitive = sorted(self._lookup, key=len, reverse=True)  # longest alias wins
        self._insensitive_re = re.compile("|".join(_boundary_pattern(a) for a in insensitive), re.IGNORECASE)
        self._exact: list[tuple[re.Pattern[str], str]] = [
            (re.compile(_boundary_pattern(e)), s.name) for s in self.skills.values() for e in s.exact
        ]

    @staticmethod
    def load(path: str | None = None) -> Taxonomy:
        if path:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        else:
            raw = tomllib.loads(resources.files("jobpulse.data").joinpath("taxonomy.toml").read_text("utf-8"))
        return Taxonomy(
            Skill(
                name=name,
                category=v.get("category", "other"),
                aliases=tuple(v.get("aliases", ())),
                exact=tuple(v.get("exact", ())),
            )
            for name, v in raw.items()
        )

    def canonical(self, name: str) -> str | None:
        """Map a free-form skill name onto the taxonomy ("k8s" -> "Kubernetes"), or None."""
        cleaned = name.strip()
        if not cleaned:
            return None
        for s in self.skills.values():
            if cleaned in s.exact:
                return s.name
        hit = self._lookup.get(cleaned.lower())
        if hit:
            return hit
        # LLMs often add qualifiers: "Python programming", "AWS (S3, Lambda)". Fall back to
        # finding exactly one taxonomy skill inside the phrase.
        found = self.find(cleaned)
        return next(iter(found)) if len(found) == 1 else None

    def find(self, text: str) -> set[str]:
        """All canonical skills mentioned anywhere in `text` (keyword matching)."""
        found = {self._lookup[m.group(0).lower()] for m in self._insensitive_re.finditer(text)}
        for pattern, name in self._exact:
            for m in pattern.finditer(text):
                before = text[max(0, m.start() - 6) : m.start()]
                after = text[m.end() : m.end() + 6]
                if _LIST_CONTEXT.search(before) or _LIST_CONTEXT.search(after):
                    found.add(name)
                    break
        return found

    def category(self, name: str) -> str:
        s = self.skills.get(name)
        return s.category if s else "other"


@lru_cache(maxsize=1)
def default_taxonomy() -> Taxonomy:
    return Taxonomy.load()
