"""Domain models shared by every pipeline step, locally and on AWS.

All models are frozen dataclasses with explicit `to_dict` / `from_dict` so they
can be stored as JSON documents in either SQLite or DynamoDB without an ORM.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


def content_hash(text: str) -> str:
    """Stable hash of JD text; the key of the LLM extraction cache."""
    normalized = " ".join(text.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Constraints:
    """Hard eligibility signals found by rules (never by the LLM)."""

    degree_levels: tuple[str, ...] = ()  # e.g. ("Bachelor's", "Master's")
    requires_phd: bool = False
    grad_window: str | None = None  # e.g. "Dec 2027 - Jun 2028"
    no_sponsorship: bool = False
    citizenship_required: bool = False
    clearance_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["degree_levels"] = list(self.degree_levels)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Constraints:
        return Constraints(
            degree_levels=tuple(d.get("degree_levels", ())),
            requires_phd=bool(d.get("requires_phd", False)),
            grad_window=d.get("grad_window"),
            no_sponsorship=bool(d.get("no_sponsorship", False)),
            citizenship_required=bool(d.get("citizenship_required", False)),
            clearance_required=bool(d.get("clearance_required", False)),
        )


@dataclass(frozen=True)
class Posting:
    """One internship posting. `id` comes from the source (Simplify listing id)."""

    id: str
    company: str
    title: str
    url: str
    locations: tuple[str, ...]
    category: str
    date_posted: str  # YYYY-MM-DD (UTC) as reported by the source
    first_seen: str  # YYYY-MM-DD of the pipeline run that first ingested it
    ats: str | None = None  # greenhouse / lever / ... ; None if JD text unavailable
    jd_text: str = ""
    sponsorship: str | None = None  # source-provided field, informational
    constraints: Constraints = field(default_factory=Constraints)

    @property
    def jd_hash(self) -> str:
        return content_hash(self.jd_text)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["locations"] = list(self.locations)
        d["constraints"] = self.constraints.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Posting:
        return Posting(
            id=d["id"],
            company=d["company"],
            title=d["title"],
            url=d["url"],
            locations=tuple(d.get("locations", ())),
            category=d.get("category", ""),
            date_posted=d["date_posted"],
            first_seen=d["first_seen"],
            ats=d.get("ats"),
            jd_text=d.get("jd_text", ""),
            sponsorship=d.get("sponsorship"),
            constraints=Constraints.from_dict(d.get("constraints", {})),
        )


@dataclass(frozen=True)
class Extraction:
    """Structured requirements of one JD. Skill names are canonical taxonomy names."""

    required_skills: tuple[str, ...]
    nice_to_have: tuple[str, ...]
    domain: str
    seniority_signals: tuple[str, ...]
    constraints_mentioned: tuple[str, ...]
    method: str  # "llm" | "keyword" | "fake"
    unmapped_skills: tuple[str, ...] = ()  # skills the LLM named that the taxonomy does not know

    @property
    def all_skills(self) -> frozenset[str]:
        return frozenset(self.required_skills) | frozenset(self.nice_to_have)

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Extraction:
        return Extraction(
            required_skills=tuple(d.get("required_skills", ())),
            nice_to_have=tuple(d.get("nice_to_have", ())),
            domain=d.get("domain", ""),
            seniority_signals=tuple(d.get("seniority_signals", ())),
            constraints_mentioned=tuple(d.get("constraints_mentioned", ())),
            method=d.get("method", "unknown"),
            unmapped_skills=tuple(d.get("unmapped_skills", ())),
        )


@dataclass(frozen=True)
class TrendSignal:
    """How often a skill showed up in developer-attention sources over the last 7 days."""

    skill: str
    github_repos: int
    github_stars: int
    hn_stories: int
    hn_points: int
    score: float  # normalized 0..1 across skills on that day

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TrendSignal:
        return TrendSignal(
            skill=d["skill"],
            github_repos=int(d["github_repos"]),
            github_stars=int(d["github_stars"]),
            hn_stories=int(d["hn_stories"]),
            hn_points=int(d["hn_points"]),
            score=float(d["score"]),
        )


@dataclass(frozen=True)
class ProjectIdea:
    title: str
    why: str
    scope: str
    stack: tuple[str, ...]
    success_metrics: tuple[str, ...]
    target_skills: tuple[str, ...]
    date: str
    source: str  # "llm" | "fake" | "template"

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ProjectIdea:
        return ProjectIdea(
            title=d["title"],
            why=d["why"],
            scope=d["scope"],
            stack=tuple(d.get("stack", ())),
            success_metrics=tuple(d.get("success_metrics", ())),
            target_skills=tuple(d.get("target_skills", ())),
            date=d["date"],
            source=d.get("source", "unknown"),
        )


@dataclass
class RunRecord:
    """Summary of one pipeline run; one per date (re-running a date overwrites it)."""

    date: str
    status: str  # "running" | "succeeded" | "failed"
    started_at: str
    finished_at: str | None = None
    steps: dict[str, str] = field(default_factory=dict)  # step -> "ok" | "failed: ..."
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> RunRecord:
        return RunRecord(
            date=d["date"],
            status=d["status"],
            started_at=d["started_at"],
            finished_at=d.get("finished_at"),
            steps=dict(d.get("steps", {})),
            metrics={k: float(v) for k, v in d.get("metrics", {}).items()},
        )
