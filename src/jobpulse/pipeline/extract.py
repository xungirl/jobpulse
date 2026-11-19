"""Step 2 - extract: structured requirements per JD with an LLM, validated, cached, with fallback.

Flow per posting:
    cache hit (by JD content hash)  -> reuse, no LLM call
    else LLM -> parse + validate JSON -> retry once with the validation error
    still invalid / LLM down        -> keyword extraction (never fails the run)
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jobpulse.adapters.llm import BudgetExceeded, CountingProvider, LLMError
from jobpulse.context import Context
from jobpulse.models import Extraction, Posting
from jobpulse.observability import Metrics
from jobpulse.pipeline.common import StepResult
from jobpulse.ports import LLMProvider
from jobpulse.taxonomy import Taxonomy

PROMPT_VERSION = "extract-v1"
MAX_JD_CHARS = 12_000
MAX_ATTEMPTS = 2

SYSTEM_PROMPT = """You extract structured hiring requirements from internship job descriptions.
Reply with ONE JSON object and nothing else, with exactly these keys:
  "required_skills": [string]   technical skills the JD requires (minimum / basic qualifications)
  "nice_to_have":    [string]   technical skills listed as preferred / bonus / plus
  "domain":          string     product or research area in <= 4 words (e.g. "ads ranking", "robotics")
  "seniority_signals": [string] short phrases about level (e.g. "PhD student", "rising senior")
  "constraints":     [string]   eligibility conditions, quoted briefly (degree, grad date, visa, citizenship)
Rules:
- Only include skills that are actually written in the JD. Do not infer or add skills.
- Technical skills only (languages, frameworks, tools, platforms, technical areas) - no soft skills.
- Prefer these canonical names when one applies: {canonical}
- If a skill is not in that list, write it as it appears in the JD."""


class SchemaError(ValueError):
    pass


def _str_list(d: dict[str, Any], key: str) -> list[str]:
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise SchemaError(f'"{key}" must be a list of strings')
    return [x.strip() for x in v if x.strip()]


def parse_llm_json(raw: str) -> dict[str, Any]:
    """Extract the JSON object from a reply (tolerates ```json fences and leading prose)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise SchemaError("no JSON object in reply")
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise SchemaError(f"invalid JSON: {e.msg}") from e
    if not isinstance(obj, dict):
        raise SchemaError("top-level JSON must be an object")
    return obj


def validate_extraction(obj: dict[str, Any], tax: Taxonomy, method: str) -> Extraction:
    """Schema-check the LLM object and normalize skills onto the taxonomy."""
    missing = {"required_skills", "nice_to_have"} - obj.keys()
    if missing:
        raise SchemaError(f"missing keys: {sorted(missing)}")
    domain = obj.get("domain", "")
    if not isinstance(domain, str):
        raise SchemaError('"domain" must be a string')

    def normalize(names: list[str]) -> tuple[list[str], list[str]]:
        mapped, unmapped = [], []
        for n in names:
            c = tax.canonical(n)
            if c and c not in mapped:
                mapped.append(c)
            elif not c:
                unmapped.append(n)
        return mapped, unmapped

    required, un1 = normalize(_str_list(obj, "required_skills"))
    nice, un2 = normalize(_str_list(obj, "nice_to_have"))
    nice = [s for s in nice if s not in required]
    return Extraction(
        required_skills=tuple(required),
        nice_to_have=tuple(nice),
        domain=domain.strip()[:60],
        seniority_signals=tuple(_str_list(obj, "seniority_signals")[:5]),
        constraints_mentioned=tuple(_str_list(obj, "constraints")[:8]),
        method=method,
        unmapped_skills=tuple(dict.fromkeys(un1 + un2))[:20],
    )


_PREFERRED_HEADING = re.compile(
    r"^\W*(preferred|nice[- ]to[- ]have|bonus|pluses|desired|additional)\b.*$", re.IGNORECASE | re.MULTILINE
)


def keyword_extract(jd_text: str, tax: Taxonomy) -> Extraction:
    """Baseline / fallback: taxonomy keyword matching; text after a 'Preferred' heading = nice-to-have."""
    heading = _PREFERRED_HEADING.search(jd_text)
    head, tail = (jd_text[: heading.start()], jd_text[heading.start() :]) if heading else (jd_text, "")
    required = sorted(tax.find(head))
    nice = sorted(tax.find(tail) - set(required))
    return Extraction(tuple(required), tuple(nice), "", (), (), method="keyword")


def build_user_prompt(posting_title: str, jd_text: str) -> str:
    return f"Job title: {posting_title}\n<jd>\n{jd_text[:MAX_JD_CHARS]}\n</jd>"


def llm_extract(llm: LLMProvider, tax: Taxonomy, title: str, jd_text: str) -> Extraction:
    """LLM extraction with one corrective retry. Raises LLMError/SchemaError if both attempts fail."""
    system = SYSTEM_PROMPT.format(canonical=", ".join(sorted(tax.skills)))
    user = build_user_prompt(title, jd_text)
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        prompt = (
            user
            if last_error is None
            else (f"{user}\n\nYour previous reply was rejected ({last_error}). Reply with valid JSON only.")
        )
        try:
            raw = llm.complete_json(system, prompt, purpose=f"extract:a{attempt}")
            return validate_extraction(parse_llm_json(raw), tax, method=llm.name)
        except BudgetExceeded:
            raise
        except (LLMError, SchemaError) as e:
            last_error = e
    assert last_error is not None
    raise last_error


def cache_version(llm: LLMProvider) -> str:
    return f"{llm.name}:{PROMPT_VERSION}"


def run_extract(ctx: Context, run_date: str, workers: int = 4) -> StepResult:
    m = Metrics()
    postings = [p for p in ctx.repos.postings_for_day(run_date) if p.jd_text]
    m.set("postings_to_extract", len(postings))
    llm = CountingProvider(ctx.llm) if ctx.llm else None
    version = cache_version(llm) if llm else ""

    m.set("cache_hits", 0)
    todo: list[Posting] = []
    for p in postings:
        cached = ctx.repos.cached_extraction(p.jd_hash, version) if llm else None
        if cached:
            m.incr("cache_hits")
            ctx.repos.put_posting_extraction(p.id, cached)
        else:
            todo.append(p)
    m.set("cache_misses", len(todo))

    def work(p: Posting) -> tuple[Posting, Extraction | None, str | None]:
        if llm is None:
            return p, None, None
        try:
            return p, llm_extract(llm, ctx.taxonomy, p.title, p.jd_text), None
        except (LLMError, SchemaError) as e:  # BudgetExceeded is an LLMError too
            return p, None, f"{type(e).__name__}: {e}"

    # LLM calls run concurrently (I/O bound); all storage writes stay on this thread.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, todo))

    for p, extraction, error in results:
        if extraction is not None:
            m.incr("llm_extractions")
            ctx.repos.cache_extraction(p.jd_hash, version, extraction)
        else:
            m.incr("fallback_keyword")
            if error:
                m.incr("extract_failures")
                ctx.log.warning("extract_fallback", posting=p.id, error=error[:160])
            extraction = keyword_extract(p.jd_text, ctx.taxonomy)
        ctx.repos.put_posting_extraction(p.id, extraction)

    m.set("llm_calls", llm.count if llm else 0)
    hits = m.counters.get("cache_hits", 0)
    m.set("cache_hit_rate", round(hits / len(postings), 3) if postings else 0.0)
    ctx.log.info("extract_done", run_date=run_date, **m.counters)
    return StepResult("extract", run_date, m.counters)
