"""Bullet bank: the single source of truth for every claim a resume can make.

Accepts the jd-resume-tailor format (skills as `text = "a, b, c"`) and this
project's format (skills as `items = [...]`).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillGroup:
    key: str
    label: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class Project:
    key: str
    name: str
    desc: str
    bullets: dict[str, str]  # bullet key -> exact text


@dataclass(frozen=True)
class Bank:
    profile: dict[str, Any]
    education: list[dict[str, Any]]
    skills: dict[str, SkillGroup]
    projects: dict[str, Project]
    experience: list[dict[str, Any]]
    default_spec: dict[str, Any] = field(default_factory=dict)

    def all_bullets(self) -> set[str]:
        out = {b for p in self.projects.values() for b in p.bullets.values()}
        out |= {b for e in self.experience for b in e.get("bullets", [])}
        return out


def split_items(text: str) -> tuple[str, ...]:
    """Split "AWS (ECS, S3), Docker, SQL" on top-level commas only."""
    items, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch in ",;" and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    items.append(cur.strip())
    return tuple(i for i in items if i)


def parse_bank(toml_text: str) -> Bank:
    raw = tomllib.loads(toml_text)
    skills = {
        key: SkillGroup(
            key, v["label"], tuple(v["items"]) if "items" in v else split_items(v.get("text", ""))
        )
        for key, v in raw.get("skills", {}).items()
    }
    projects = {
        key: Project(key, v["name"], v.get("desc", ""), dict(v.get("bullets", {})))
        for key, v in raw.get("projects", {}).items()
    }
    default: dict[str, Any] = raw.get("default") or next(iter(raw.get("variants", {}).values()), {})
    return Bank(
        profile=raw.get("profile", {}),
        education=list(raw.get("education", [])),
        skills=skills,
        projects=projects,
        experience=list(raw.get("experience", [])),
        default_spec=default,
    )
