"""Extraction eval: skill precision / recall vs hand labels, LLM vs keyword baseline.

    uv run jobpulse eval --runs 3

Gold = required + nice_to_have skills in eval/labels.json (canonical taxonomy names),
labeled blind (before any system output existed). Metrics are micro-averaged over
all (JD, skill) pairs. The LLM is called fresh every run (no cache) so run-to-run
variance is real; the keyword baseline is deterministic, so it runs once.
"""

from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from jobpulse.adapters.llm import LLMError
from jobpulse.context import build_llm, load_local_config
from jobpulse.pipeline.extract import PROMPT_VERSION, SchemaError, keyword_extract, llm_extract
from jobpulse.ports import LLMProvider
from jobpulse.taxonomy import Taxonomy, default_taxonomy

Sets = dict[str, set[str]]


def prf(gold: Sets, pred: Sets) -> dict[str, float]:
    tp = sum(len(gold[k] & pred.get(k, set())) for k in gold)
    fp = sum(len(pred.get(k, set()) - gold[k]) for k in gold)
    fn = sum(len(gold[k] - pred.get(k, set())) for k in gold)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


def mean_pairwise_jaccard(runs: list[Sets]) -> float:
    """How similar repeated LLM runs are to each other (1.0 = identical skill sets)."""
    scores = []
    for a, b in combinations(runs, 2):
        for k in a:
            union = a[k] | b.get(k, set())
            scores.append(len(a[k] & b.get(k, set())) / len(union) if union else 1.0)
    return round(statistics.mean(scores), 4) if scores else 1.0


def load_eval_set(eval_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    jds = {p.stem: json.loads(p.read_text()) for p in sorted((eval_dir / "jds").glob("*.json"))}
    # JD texts are not redistributed in the repo (only URL + sha256). They live in a local,
    # gitignored cache filled by scripts/fetch_eval_jds.py, which verifies each sha256.
    cache = eval_dir / ".jd_cache"
    for key, jd in jds.items():
        if "jd_text" not in jd:
            f = cache / f"{key}.txt"
            if not f.exists():
                raise FileNotFoundError(f"{f} missing: run `uv run python scripts/fetch_eval_jds.py` first")
            jd["jd_text"] = f.read_text()
    labels = json.loads((eval_dir / "labels.json").read_text())["labels"]
    missing = set(jds) ^ set(labels)
    if missing:
        raise SystemExit(f"JDs and labels do not match: {sorted(missing)}")
    return jds, labels


def llm_run(llm: LLMProvider, tax: Taxonomy, jds: dict[str, dict[str, Any]]) -> tuple[Sets, Sets, int]:
    def one(item: tuple[str, dict[str, Any]]) -> tuple[str, set[str], set[str], bool]:
        key, jd = item
        try:
            ex = llm_extract(llm, tax, jd["title"], jd["jd_text"])
            return key, set(ex.all_skills), set(ex.required_skills), True
        except (LLMError, SchemaError):
            return key, set(), set(), False  # counted as a miss: no silent keyword fallback in eval

    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(one, jds.items()))
    return ({k: s for k, s, _, _ in out}, {k: r for k, _, r, _ in out}, sum(not ok for *_, ok in out))


def summarize(per_run: list[dict[str, float]]) -> dict[str, float]:
    out = {}
    for metric in ("precision", "recall", "f1"):
        vals = [r[metric] for r in per_run]
        out[f"{metric}_mean"] = round(statistics.mean(vals), 4)
        out[f"{metric}_std"] = round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0
        out[f"{metric}_min"] = min(vals)
        out[f"{metric}_max"] = max(vals)
    return out


def run_eval(eval_dir: Path, runs: int, provider: str, out_path: Path) -> int:
    tax = default_taxonomy()
    jds, labels = load_eval_set(eval_dir)
    gold = {k: set(v["required"]) | set(v["nice_to_have"]) for k, v in labels.items()}
    gold_required = {k: set(v["required"]) for k, v in labels.items()}

    keyword = {k: keyword_extract(jd["jd_text"], tax) for k, jd in jds.items()}
    kw_all = {k: set(e.all_skills) for k, e in keyword.items()}
    kw_req = {k: set(e.required_skills) for k, e in keyword.items()}

    llm = build_llm(provider, load_local_config().get("env_file"), Path(".jobpulse/llm_ledger.jsonl"))
    assert llm is not None
    pred_dir = eval_dir / "predictions"
    pred_dir.mkdir(exist_ok=True)
    (pred_dir / "keyword.json").write_text(json.dumps({k: sorted(v) for k, v in kw_all.items()}, indent=1))

    llm_all: list[Sets] = []
    per_run, per_run_req, failures = [], [], []
    for i in range(runs):
        all_sets, req_sets, failed = llm_run(llm, tax, jds)
        llm_all.append(all_sets)
        per_run.append(prf(gold, all_sets))
        per_run_req.append(prf(gold_required, req_sets))
        failures.append(failed)
        (pred_dir / f"{llm.name}_run{i + 1}.json").write_text(
            json.dumps({k: sorted(v) for k, v in all_sets.items()}, indent=1)
        )
        print(f"run {i + 1}: {per_run[-1]}  failed={failed}", flush=True)

    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": llm.name,
        "model": getattr(llm, "model", llm.name),
        "prompt_version": PROMPT_VERSION,
        "n_jds": len(jds),
        "n_gold_labels": sum(len(v) for v in gold.values()),
        "runs": runs,
        "keyword": {"all_skills": prf(gold, kw_all), "required_only": prf(gold_required, kw_req)},
        "llm": {
            "all_skills": summarize(per_run),
            "required_only": summarize(per_run_req),
            "per_run": per_run,
            "run_to_run_jaccard": mean_pairwise_jaccard(llm_all),
            "failed_extractions_per_run": failures,
        },
    }
    out_path.write_text(json.dumps(results, indent=1))
    print(
        json.dumps(
            {
                "keyword": results["keyword"]["all_skills"],
                "llm": results["llm"]["all_skills"],
                "jaccard": results["llm"]["run_to_run_jaccard"],
            },
            indent=1,
        )
    )
    return 0
