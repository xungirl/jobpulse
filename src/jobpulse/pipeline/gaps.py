"""Step 5 - gaps: in-demand skills the bank has no evidence for -> 3 portfolio project ideas/day.

Ranking: priority = demand share (30d) x (1 + trend score). Demand dominates;
trend breaks ties toward technologies developers are paying attention to.
The LLM writes the idea; the demand numbers in "why" are filled in by code,
so an idea can never cite a statistic we did not measure.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from jobpulse.adapters.llm import CountingProvider, LLMError
from jobpulse.bank import Bank, parse_bank
from jobpulse.context import Context
from jobpulse.models import ProjectIdea
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.pipeline.extract import SchemaError, parse_llm_json
from jobpulse.pipeline.market import demand_table
from jobpulse.taxonomy import Taxonomy

MIN_COUNT_30D = 2
TOP_GAPS_IN_PROMPT = 8
SIMILAR_TITLE = 0.5
REPEAT_WINDOW_DAYS = 14

IDEAS_SYSTEM = """You propose portfolio projects for a CS master's student applying to SWE / AI internships.
Reply with ONE JSON object: {"ideas": [ {"title": str, "why": str, "scope": str, "stack": [str],
"success_metrics": [str], "target_skills": [str]} ]} with exactly 3 ideas.
Rules:
- Each idea must close one or more of the listed skill gaps; put those exact gap names in target_skills.
- scope: what to build in 2-3 weeks, concrete and small enough to finish.
- success_metrics: 2-4 measurable targets the student can verify themselves (tests, latency, accuracy...).
- why: one sentence on what the skill is used for in industry. Do NOT include any numbers or statistics.
- Do not repeat or lightly reword any existing project or previous idea listed."""


def evidenced_skills(bank: Bank, tax: Taxonomy) -> set[str]:
    texts = [", ".join(g.items) for g in bank.skills.values()]
    texts += [f"{p.desc} {' '.join(p.bullets.values())}" for p in bank.projects.values()]
    texts += [" ".join(e.get("bullets", [])) for e in bank.experience]
    return {s for t in texts for s in tax.find(t)}


def rank_gaps(
    demand: list[dict[str, Any]], trend_scores: dict[str, float], evidenced: set[str]
) -> list[dict[str, Any]]:
    gaps = []
    for r in demand:
        if r["skill"] in evidenced or r.get("count_30d", 0) < MIN_COUNT_30D:
            continue
        trend = trend_scores.get(r["skill"], 0.0)
        gaps.append({**r, "trend_score": trend, "priority": round(r.get("share_30d", 0) * (1 + trend), 4)})
    return sorted(gaps, key=lambda g: (-g["priority"], g["skill"]))


_GENERIC = {
    "minimal",
    "service",
    "tests",
    "test",
    "benchmark",
    "with",
    "and",
    "the",
    "for",
    "starter",
    "using",
    "based",
    "system",
    "tool",
    "app",
    "project",
    "simple",
    "mini",
    "small",
}


def _title_tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#]+", title.lower()) if len(t) > 2 and t not in _GENERIC}


def is_duplicate(
    title: str, targets: tuple[str, ...], taken_titles: list[str], recent: list[ProjectIdea]
) -> bool:
    """Same gap set proposed recently (`recent`), or a title too similar to anything taken."""
    if any(set(targets) == set(r.target_skills) for r in recent):
        return True
    toks = _title_tokens(title)
    for other in taken_titles:
        o = _title_tokens(other)
        if toks and o and len(toks & o) / len(toks | o) >= SIMILAR_TITLE:
            return True
    return False


def stats_sentence(gap: dict[str, Any]) -> str:
    s = f"{gap['skill']}: in {gap['share_30d']:.0%} of {gap['postings_30d']} postings over 30 days"
    if gap.get("delta_7d") is not None:
        s += f" ({gap['delta_7d'] * 100:+.1f} pp vs previous 7 days)"
    return s + f"; trend score {gap['trend_score']:.2f}."


def _parse_ideas(raw: str, gap_names: set[str], run_date: str, source: str) -> list[ProjectIdea]:
    obj = parse_llm_json(raw)
    ideas = obj.get("ideas")
    if not isinstance(ideas, list):
        raise SchemaError('"ideas" must be a list')
    out = []
    for d in ideas:
        if not isinstance(d, dict) or not isinstance(d.get("title"), str):
            continue
        targets = tuple(t for t in d.get("target_skills", []) if isinstance(t, str) and t in gap_names)
        if not targets:
            continue  # an idea that closes no measured gap is off-task
        out.append(
            ProjectIdea(
                title=d["title"].strip()[:90],
                why=str(d.get("why", "")).strip()[:300],
                scope=str(d.get("scope", "")).strip()[:500],
                stack=tuple(str(x) for x in d.get("stack", []))[:8],
                success_metrics=tuple(str(x) for x in d.get("success_metrics", []))[:4],
                target_skills=targets,
                date=run_date,
                source=source,
            )
        )
    return out


def template_idea(gap: dict[str, Any], run_date: str) -> ProjectIdea:
    skill = gap["skill"]
    return ProjectIdea(
        title=f"Minimal {skill} service with tests and a benchmark",
        why=f"{skill} is requested by postings this month and is not yet evidenced in the bullet bank.",
        scope=f"Build the smallest useful service that depends on {skill}, with CI and a README.",
        stack=(skill, "Python", "pytest"),
        success_metrics=("CI green on every commit", "one benchmark number recorded in the README"),
        target_skills=(skill,),
        date=run_date,
        source="template",
    )


def run_gaps(ctx: Context, run_date: str) -> StepResult:
    m = Metrics()
    bank = parse_bank(ctx.bank_toml)
    evidenced = evidenced_skills(bank, ctx.taxonomy)
    demand = demand_table(ctx.repos.demand_series(), run_date, ctx.taxonomy)
    trend_scores = {s.skill: s.score for s in ctx.repos.trends(run_date)}
    gaps = rank_gaps(demand, trend_scores, evidenced)
    m.set("gaps_found", len(gaps))
    previous = [i for i in ctx.repos.all_ideas() if i.date < run_date]
    window_start = (date.fromisoformat(run_date) - timedelta(days=REPEAT_WINDOW_DAYS)).isoformat()
    recent = [i for i in previous if i.date >= window_start]
    # Gaps already targeted by an idea in the last 14 days are skipped, so the same top gaps
    # do not get proposed (and then deduped away) day after day.
    covered = {s for i in recent for s in i.target_skills}
    open_gaps = [g for g in gaps if g["skill"] not in covered]
    m.set("gaps_recently_covered", len(gaps) - len(open_gaps))
    top = open_gaps[:TOP_GAPS_IN_PROMPT]
    by_skill = {g["skill"]: g for g in top}
    existing = [f"{p.name} {p.desc}" for p in bank.projects.values()]
    ideas: list[ProjectIdea] = []
    llm = CountingProvider(ctx.llm) if ctx.llm else None
    if llm and top:
        user = (
            "Skill gaps (in demand, not evidenced):\n"
            + "\n".join(
                f"- {g['skill']} (demand {g['share_30d']:.0%}, trend {g['trend_score']:.2f})" for g in top
            )
            + "\n\nExisting projects:\n"
            + "\n".join(f"- {t}" for t in existing)
            + "\n\nPrevious ideas (do not repeat):\n"
            + "\n".join(f"- {i.title}" for i in previous[-30:])
        )
        for attempt in range(2):
            try:
                raw = llm.complete_json(IDEAS_SYSTEM, user, purpose=f"ideas:a{attempt}")
                ideas = _parse_ideas(raw, set(by_skill), run_date, llm.name)
                break
            except (LLMError, SchemaError) as e:
                m.incr("ideas_llm_errors")
                ctx.log.warning("ideas_llm_error", error=str(e)[:160])

    kept: list[ProjectIdea] = []

    def taken() -> list[str]:
        return existing + [i.title for i in previous] + [k.title for k in kept]  # titles: all-time

    for idea in ideas:
        if is_duplicate(idea.title, idea.target_skills, taken(), recent + kept):
            m.incr("ideas_deduped")
            continue
        stats = " ".join(stats_sentence(by_skill[s]) for s in idea.target_skills if s in by_skill)
        kept.append(
            ProjectIdea(
                idea.title,
                f"{idea.why} Measured demand - {stats}",
                idea.scope,
                idea.stack,
                idea.success_metrics,
                idea.target_skills,
                idea.date,
                idea.source,
            )
        )
    for g in top:  # top up with deterministic templates if the LLM gave fewer than needed
        if len(kept) >= ctx.settings.ideas_per_day:
            break
        t = template_idea(g, run_date)
        if not is_duplicate(t.title, t.target_skills, taken(), recent + kept):
            kept.append(
                ProjectIdea(
                    t.title,
                    f"{t.why} Measured demand - {stats_sentence(g)}",
                    t.scope,
                    t.stack,
                    t.success_metrics,
                    t.target_skills,
                    t.date,
                    t.source,
                )
            )
    kept = kept[: ctx.settings.ideas_per_day]
    ctx.repos.put_ideas(run_date, kept)
    m.set("ideas_kept", len(kept))
    m.set("llm_calls", llm.count if llm else 0)

    report = ctx.repos.report(run_date)
    report["gaps"] = [
        {k: g[k] for k in ("skill", "category", "share_30d", "count_30d", "trend_score", "priority")}
        for g in gaps[:15]
    ]
    report["evidenced_skills"] = sorted(evidenced)
    ctx.repos.put_report(run_date, report)
    ctx.log.info("gaps_done", run_date=run_date, **m.counters)
    return StepResult("gaps", run_date, m.counters)


def ideas_to_json(ideas: list[ProjectIdea]) -> str:
    return json.dumps([i.to_dict() for i in ideas], indent=1)
