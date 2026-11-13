"""Replay the frozen incident fixtures against an LLM and score Diagnose and the safe-actions proposer.

Usage (repo root, backend virtualenv, LLM_API_KEY or GROQ_API_KEY set):

    make eval                                         # LLM_MODEL from the env/.env (default openai/gpt-oss-120b)
    make eval LLM_MODEL=openai/gpt-oss-20b
    PYTHONPATH=.:backend python -m ml.llm_eval.run --variants full,no_rag,no_signals
    PYTHONPATH=.:backend python -m ml.llm_eval.run --dry-run   # no API calls: fixtures, prompts, baselines

Calls go through the API's own LLMExplainer and ActionProposer (same prompts,
temperature and response format). Requests are paced under --tpm tokens per
minute and 429s are retried after the server's suggested wait; a daily-quota
429 stops the run and the partial report says so.

Writes ml/reports/llm_eval/<model>.json (every call) and <model>.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.llm_eval.fixtures import FIXTURE_DIR, Fixture, load_all
from ml.llm_eval.scoring import (
    reason_lookup_category,
    schema_check,
    score_category,
    score_hallucination,
    score_remediation,
    score_retrieval,
)
from ml.llm_eval.taxonomy import CATEGORIES

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "llm_eval"
VARIANTS = ("full", "no_rag", "no_signals")
# USD per 1M tokens (input, output), Groq list prices from console.groq.com/docs/models, checked 2026-11-13.
PRICES = {"openai/gpt-oss-120b": (0.15, 0.60), "openai/gpt-oss-20b": (0.075, 0.30)}


class QuotaExhausted(Exception):
    pass


# ------------------------------------------------------------------ pacing
class Pacer:
    """Keep the rolling 60s token count under the per-minute limit; retry 429s."""

    def __init__(self, tpm: int) -> None:
        self.tpm = tpm
        self.window: deque[tuple[float, int]] = deque()
        self.waited_s = 0.0
        self.retries = 0

    def _used(self) -> int:
        now = time.time()
        while self.window and now - self.window[0][0] > 60:
            self.window.popleft()
        return sum(t for _, t in self.window)

    def before(self, estimate: int) -> None:
        if self.tpm <= 0:
            return
        estimate = min(estimate, self.tpm)
        while self._used() + estimate > self.tpm:
            pause = max(1.0, 60 - (time.time() - self.window[0][0]) + 0.5)
            self.waited_s += pause
            time.sleep(pause)

    def after(self, tokens: int) -> None:
        self.window.append((time.time(), tokens))

    def call(self, fn, estimate: int):
        for attempt in range(8):
            self.before(estimate)
            t0 = time.perf_counter()
            try:
                out = fn()
            except Exception as exc:  # noqa: BLE001
                text = str(exc)
                if "per day" in text.lower() or "(TPD)" in text or "(RPD)" in text:
                    raise QuotaExhausted(text[:300]) from exc
                if "429" in text or "rate_limit" in text.lower() or "rate limit" in text.lower():
                    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", text)
                    wait = (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 20.0
                    self.retries += 1
                    self.waited_s += wait + 1
                    self.after(estimate)
                    time.sleep(wait + 1)
                    continue
                raise
            return out, time.perf_counter() - t0
        raise RuntimeError("gave up after repeated rate limiting")


# ------------------------------------------------------------------ variants
def diagnose_inputs(fx: Fixture, variant: str):
    retrieved = None if variant == "no_rag" else fx.retrieved
    anomaly = fx.anomaly
    if variant == "no_signals" and anomaly is not None:
        anomaly = anomaly.model_copy(update={"lifecycle_signals": []})
    return fx.pod, fx.events, fx.logs, retrieved, anomaly


def propose_signals(fx: Fixture, variant: str) -> list[str]:
    return [] if variant == "no_signals" else fx.signals


def runs_variant(fx: Fixture, task: str, variant: str) -> bool:
    if variant == "full":
        return True
    if variant == "no_rag":
        return task == "diagnose" and bool(fx.retrieved)
    if variant == "no_signals":
        return fx.has_signals if task == "diagnose" else bool(fx.signals)
    return False


# ------------------------------------------------------------------ one call each
def run_diagnose(fx: Fixture, variant: str, explainer, pacer: Pacer) -> dict[str, Any]:
    from app.llm_explainer import SYSTEM_PROMPT, build_user_prompt

    pod, events, logs, retrieved, anomaly = diagnose_inputs(fx, variant)
    prompt = build_user_prompt(pod, events, logs, retrieved, anomaly)
    estimate = (len(SYSTEM_PROMPT) + len(prompt)) // 4 + 900
    rec: dict[str, Any] = {"case": fx.id, "task": "diagnose", "variant": variant}
    try:
        run, latency = pacer.call(lambda: explainer.explain_detailed(pod, events, logs, retrieved, anomaly), estimate)
    except QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001 — record and move on
        rec.update(error=str(exc)[:500])
        return rec
    pacer.after((run.prompt_tokens or 0) + (run.completion_tokens or 0) or estimate)
    valid, schema_note = schema_check(run.raw_text)
    d = run.diagnosis
    rec.update(
        latency_s=round(latency, 3),
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        schema_valid=valid,
        schema_note=schema_note,
        parsed=run.parsed,
        diagnosis=d.model_dump(),
        category=score_category(fx.labels["category"], d.likely_root_cause),
        hallucination=score_hallucination(d.kubectl_commands, prompt),
    )
    return rec


def run_propose(fx: Fixture, variant: str, proposer, pacer: Pacer) -> dict[str, Any]:
    from app.actions.models import ActionRequest
    from app.actions.policy import HARD_DENIED_NAMESPACES, IncidentScope, PolicyConfig, TargetState, validate
    from app.actions.proposer import SYSTEM_PROMPT, build_context

    context = build_context(fx.pod, fx.events, fx.logs, fx.deployment, propose_signals(fx, variant))
    tools_config = PolicyConfig()  # only scale_min/max are used to describe the tools
    estimate = (len(SYSTEM_PROMPT) + len(context)) // 4 + 1100
    rec: dict[str, Any] = {"case": fx.id, "task": "propose", "variant": variant}
    try:
        result, latency = pacer.call(lambda: proposer.propose(context, tools_config), estimate)
    except QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001
        rec.update(error=str(exc)[:500])
        return rec
    pacer.after((result.prompt_tokens or 0) + (result.completion_tokens or 0) or estimate)
    kinds = [r.kind for r in result.requests]
    deployment_name = fx.deployment.name if fx.deployment else None
    policy = PolicyConfig(enabled=True, allowed_namespaces=frozenset({fx.namespace}),
                          denied_namespaces=HARD_DENIED_NAMESPACES)
    scope = IncidentScope(namespace=fx.namespace, pod=fx.pod.name, deployment=deployment_name)
    target = TargetState(deployment=fx.deployment, pod=fx.pod_state)
    violations = [validate(ActionRequest(kind=r.kind, params=r.params), policy, scope, target) for r in result.requests]
    rec.update(
        latency_s=round(latency, 3),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        proposals=[{"kind": r.kind, "params": r.params, "rationale": r.rationale} for r in result.requests],
        note=result.note,
        remediation=score_remediation(fx.labels["expected_actions"], kinds, [r.params for r in result.requests],
                                      fx.namespace, deployment_name, fx.pod.name),
        policy_violations=violations,
        policy_blocked=sum(1 for v in violations if v),
    )
    return rec


# ------------------------------------------------------------------ aggregation
def pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = q * (len(values) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (idx - lo), 2)


def wilson(n: int, d: int, z: float = 1.96) -> tuple[float, float] | None:
    if not d:
        return None
    p = n / d
    denom = 1 + z * z / d
    centre = (p + z * z / (2 * d)) / denom
    half = z * ((p * (1 - p) / d + z * z / (4 * d * d)) ** 0.5) / denom
    return round(100 * (centre - half), 1), round(100 * (centre + half), 1)


def summarize(records: list[dict], fixtures: dict[str, Fixture], model: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for task in ("diagnose", "propose"):
        for variant in VARIANTS:
            recs = [r for r in records if r["task"] == task and r["variant"] == variant]
            if not recs:
                continue
            ok = [r for r in recs if "error" not in r]
            s: dict[str, Any] = {"cases": len(recs), "errors": len(recs) - len(ok)}
            lat = [r["latency_s"] for r in ok]
            s["latency_p50_s"], s["latency_p95_s"] = quantile(lat, 0.5), quantile(lat, 0.95)
            pt = sum(r.get("prompt_tokens") or 0 for r in ok)
            ct = sum(r.get("completion_tokens") or 0 for r in ok)
            s["prompt_tokens"], s["completion_tokens"] = pt, ct
            if model in PRICES:
                pin, pout = PRICES[model]
                s["cost_usd"] = round(pt / 1e6 * pin + ct / 1e6 * pout, 4)
            if task == "diagnose":
                n_cat = sum(r["category"]["correct"] for r in ok)
                s["category_correct"] = n_cat
                s["category_accuracy"] = pct(n_cat, len(recs))
                s["category_accuracy_ci95"] = wilson(n_cat, len(recs))
                s["schema_valid"] = pct(sum(r["schema_valid"] for r in ok), len(recs))
                s["hallucination_rate"] = pct(sum(r["hallucination"]["hallucinated"] for r in ok), len(ok))
                s["placeholder_rate"] = pct(sum(bool(r["hallucination"]["placeholders"]) for r in ok), len(ok))
                conf_ok = [r["diagnosis"]["confidence"] for r in ok if r["category"]["correct"]]
                conf_bad = [r["diagnosis"]["confidence"] for r in ok if not r["category"]["correct"]]
                s["mean_confidence_correct"] = round(statistics.mean(conf_ok), 3) if conf_ok else None
                s["mean_confidence_wrong"] = round(statistics.mean(conf_bad), 3) if conf_bad else None
            else:
                n_rem = sum(r["remediation"]["correct"] for r in ok)
                s["remediation_correct"] = n_rem
                s["remediation_accuracy"] = pct(n_rem, len(recs))
                s["remediation_accuracy_ci95"] = wilson(n_rem, len(recs))
                s["target_errors"] = sum(bool(r["remediation"]["target_errors"]) for r in ok)
                s["policy_blocked_proposals"] = sum(r["policy_blocked"] for r in ok)
                s["proposals"] = sum(len(r["proposals"]) for r in ok)
                s["no_action_rate"] = pct(sum(r["remediation"]["primary"] == "no_action" for r in ok), len(ok))
            out[f"{task}/{variant}"] = s

    # Paired ablations: same cases, full vs variant.
    ablations = {}
    for task, key in (("diagnose", "category"), ("propose", "remediation")):
        full = {r["case"]: r for r in records if r["task"] == task and r["variant"] == "full" and "error" not in r}
        for variant in ("no_rag", "no_signals"):
            other = {r["case"]: r for r in records if r["task"] == task and r["variant"] == variant and "error" not in r}
            common = sorted(set(full) & set(other))
            if not common:
                continue
            a = sum(full[c][key]["correct"] for c in common)
            b = sum(other[c][key]["correct"] for c in common)
            ablations[f"{task}/{variant}"] = {
                "cases": len(common),
                "full_correct": a,
                "variant_correct": b,
                "full_accuracy": pct(a, len(common)),
                "variant_accuracy": pct(b, len(common)),
                "flipped_to_wrong": [c for c in common if full[c][key]["correct"] and not other[c][key]["correct"]],
                "flipped_to_right": [c for c in common if not full[c][key]["correct"] and other[c][key]["correct"]],
            }
            if task == "diagnose":
                ablations[f"{task}/{variant}"]["mean_prompt_tokens_full"] = round(
                    statistics.mean(full[c]["prompt_tokens"] or 0 for c in common))
                ablations[f"{task}/{variant}"]["mean_prompt_tokens_variant"] = round(
                    statistics.mean(other[c]["prompt_tokens"] or 0 for c in common))
    out["ablations"] = ablations

    # Breakdowns for the full variant.
    by_cat: dict[str, Counter] = defaultdict(Counter)
    by_diff: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        if r["variant"] != "full" or "error" in r:
            continue
        fx = fixtures[r["case"]]
        key = "category" if r["task"] == "diagnose" else "remediation"
        by_cat[fx.labels["category"]][f"{r['task']}_n"] += 1
        by_cat[fx.labels["category"]][f"{r['task']}_ok"] += int(r[key]["correct"])
        by_diff[fx.labels["difficulty"]][f"{r['task']}_n"] += 1
        by_diff[fx.labels["difficulty"]][f"{r['task']}_ok"] += int(r[key]["correct"])
    out["by_category"] = {k: dict(v) for k, v in sorted(by_cat.items())}
    out["by_difficulty"] = {k: dict(v) for k, v in sorted(by_diff.items())}
    return out


def baselines(fixtures: list[Fixture]) -> dict[str, Any]:
    """Model-free reference points computed from the same fixtures."""
    n = len(fixtures)
    lookup = sum(reason_lookup_category(f.raw["diagnose_input"]["pod"]) == f.labels["category"] for f in fixtures)
    always_none = sum("no_action" in f.labels["expected_actions"] for f in fixtures)
    rollback_rule = sum(
        (("rollback_deployment" if f.deployment and f.deployment.previous_revision else "no_action")
         in f.labels["expected_actions"])
        for f in fixtures
    )
    retrieval = [score_retrieval(f.labels["expected_runbooks"], [c.source for c in f.retrieved]) for f in fixtures]
    retrieval = [r for r in retrieval if r is not None]
    return {
        "cases": n,
        "category_reason_lookup_accuracy": pct(lookup, n),
        "remediation_always_no_action_accuracy": pct(always_none, n),
        "remediation_rollback_if_previous_revision_accuracy": pct(rollback_rule, n),
        "retrieval": {
            "cases_with_expected_runbook": len(retrieval),
            "hit_at_k": pct(sum(r["hit"] for r in retrieval), len(retrieval)),
            "hit_at_1": pct(sum(r["hit_at_1"] for r in retrieval), len(retrieval)),
            "mrr": round(statistics.mean(r["rr"] for r in retrieval), 3) if retrieval else None,
            "misses": [f.id for f in fixtures if (s := score_retrieval(f.labels["expected_runbooks"],
                                                                       [c.source for c in f.retrieved])) and not s["hit"]],
        },
    }


def dataset_summary(fixtures: list[Fixture]) -> dict[str, Any]:
    return {
        "cases": len(fixtures),
        "by_category": dict(Counter(f.labels["category"] for f in fixtures).most_common()),
        "by_difficulty": dict(Counter(f.labels["difficulty"] for f in fixtures).most_common()),
        "by_expected_action": dict(Counter(f.labels["expected_actions"][0] for f in fixtures).most_common()),
        "with_lifecycle_signals": sum(f.has_signals for f in fixtures),
        "with_previous_revision": sum(bool(f.deployment and f.deployment.previous_revision) for f in fixtures),
    }


# ------------------------------------------------------------------ report
def fmt(v: Any, suffix: str = "") -> str:
    return "n/a" if v is None else f"{v}{suffix}"


def render_markdown(report: dict[str, Any]) -> str:
    m = report["model"]
    s = report["summary"]
    lines = [
        f"# LLM eval: `{m}`",
        "",
        f"Run {report['started_at']} → {report['finished_at']} (UTC) on {report['dataset']['cases']} frozen incidents "
        f"from `ml/data/llm_eval/cases/`. Status: **{report['status']}**.",
        "",
    ]
    if report.get("stopped_reason"):
        lines += [f"> Stopped early: {report['stopped_reason']}", ""]
    d = s.get("diagnose/full")
    p = s.get("propose/full")
    lines += ["## Headline (full context)", "", "| Metric | Value |", "|---|---|"]
    if d:
        lines += [
            f"| Root-cause category accuracy | {d['category_correct']}/{d['cases']} = {fmt(d['category_accuracy'], '%')} (95% CI {fmt(d['category_accuracy_ci95'])}) |",
            f"| JSON/schema valid | {fmt(d['schema_valid'], '%')} |",
            f"| Hallucinated resource in a kubectl command | {fmt(d['hallucination_rate'], '%')} of diagnoses |",
            f"| Diagnose latency p50 / p95 | {fmt(d['latency_p50_s'], 's')} / {fmt(d['latency_p95_s'], 's')} |",
            f"| Mean confidence when right / wrong | {fmt(d['mean_confidence_correct'])} / {fmt(d['mean_confidence_wrong'])} |",
        ]
    if p:
        lines += [
            f"| Remediation (first proposed action) correct | {p['remediation_correct']}/{p['cases']} = {fmt(p['remediation_accuracy'], '%')} (95% CI {fmt(p['remediation_accuracy_ci95'])}) |",
            f"| Proposals targeting the wrong object | {p['target_errors']} |",
            f"| Proposals the policy engine would block | {p['policy_blocked_proposals']}/{p['proposals']} |",
            f"| Propose latency p50 / p95 | {fmt(p['latency_p50_s'], 's')} / {fmt(p['latency_p95_s'], 's')} |",
        ]
    b = report["baselines"]
    r = b["retrieval"]
    lines += [
        f"| RAG: correct runbook in top-5 | {fmt(r['hit_at_k'], '%')} of {r['cases_with_expected_runbook']} cases (hit@1 {fmt(r['hit_at_1'], '%')}, MRR {r['mrr']}) |",
        "",
        "## Model-free baselines on the same cases",
        "",
        "| Baseline | Accuracy |",
        "|---|---|",
        f"| Category from a pod-reason lookup table | {fmt(b['category_reason_lookup_accuracy'], '%')} |",
        f"| Remediation: always `no_action` | {fmt(b['remediation_always_no_action_accuracy'], '%')} |",
        f"| Remediation: rollback if a previous revision exists, else `no_action` | {fmt(b['remediation_rollback_if_previous_revision_accuracy'], '%')} |",
        "",
        "## All runs",
        "",
        "| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key, v in s.items():
        if "/" not in key or key.startswith(("ablations", "by_")):
            continue
        acc = v.get("category_accuracy", v.get("remediation_accuracy"))
        lines.append(
            f"| {key} | {v['cases']} | {v['errors']} | {fmt(acc, '%')} | {fmt(v['latency_p50_s'], 's')} | "
            f"{fmt(v['latency_p95_s'], 's')} | {v['prompt_tokens']} | {v['completion_tokens']} | {fmt(v.get('cost_usd'))} |"
        )
    if s.get("ablations"):
        lines += ["", "## Ablations (paired, same cases)", "",
                  "| Ablation | Cases | Full | Without | Flipped to wrong | Flipped to right |", "|---|---|---|---|---|---|"]
        for key, a in s["ablations"].items():
            lines.append(
                f"| {key} | {a['cases']} | {a['full_correct']} ({fmt(a['full_accuracy'], '%')}) | "
                f"{a['variant_correct']} ({fmt(a['variant_accuracy'], '%')}) | {', '.join(a['flipped_to_wrong']) or '-'} | "
                f"{', '.join(a['flipped_to_right']) or '-'} |"
            )
    lines += ["", "## By category (full context)", "", "| Category | Diagnose | Propose |", "|---|---|---|"]
    for cat, c in s.get("by_category", {}).items():
        lines.append(f"| {cat} | {c.get('diagnose_ok', 0)}/{c.get('diagnose_n', 0)} | {c.get('propose_ok', 0)}/{c.get('propose_n', 0)} |")
    lines += ["", "## By difficulty (full context)", "", "| Difficulty | Diagnose | Propose |", "|---|---|---|"]
    for diff, c in s.get("by_difficulty", {}).items():
        lines.append(f"| {diff} | {c.get('diagnose_ok', 0)}/{c.get('diagnose_n', 0)} | {c.get('propose_ok', 0)}/{c.get('propose_n', 0)} |")
    lines += ["", "## Per case (full context)", "",
              "| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |",
              "|---|---|---|---|---|---|---|"]
    by_case: dict[str, dict] = defaultdict(dict)
    for rec in report["records"]:
        if rec["variant"] == "full":
            by_case[rec["case"]][rec["task"]] = rec
    for case_id in sorted(by_case):
        dr, pr = by_case[case_id].get("diagnose", {}), by_case[case_id].get("propose", {})
        label = report["labels"][case_id]
        root = (dr.get("diagnosis") or {}).get("likely_root_cause", dr.get("error", ""))
        root = root.replace("|", "\\|").replace("\n", " ")
        root = root[:110] + ("…" if len(root) > 110 else "")
        cat_ok = "✅" if dr.get("category", {}).get("correct") else "❌"
        proposed = ", ".join(p_["kind"] for p_ in pr.get("proposals", [])) or ("no_action" if pr and "error" not in pr else pr.get("error", "")[:40])
        act_ok = "✅" if pr.get("remediation", {}).get("correct") else "❌"
        lines.append(f"| `{case_id}` | {label['category']} | {root} | {cat_ok} | {' / '.join(label['expected_actions'])} | {proposed} | {act_ok} |")
    halluc = [(rec["case"], rec["hallucination"]["unknown_refs"]) for rec in report["records"]
              if rec["task"] == "diagnose" and rec["variant"] == "full" and rec.get("hallucination", {}).get("hallucinated")]
    if halluc:
        lines += ["", "## Unknown resources referenced in kubectl commands (full context)", ""]
        lines += [f"- `{c}`: {', '.join(refs)}" for c, refs in halluc]
    lines += [
        "",
        "## How this is scored",
        "",
        "- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.",
        "- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.",
        "- **Schema**: the raw reply must be one JSON object with the five keys and right types.",
        "- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. "
        "Placeholders like `<svc>` are counted separately, not as hallucinations.",
        "- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). "
        f"Pacing waited {report['pacing']['waited_s']}s and retried {report['pacing']['retries']} rate-limited calls.",
        "- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ main
def slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9.-]+", "_", model).strip("_")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=None, help="Defaults to LLM_MODEL / the API default")
    parser.add_argument("--variants", default="full", help=f"Comma-separated: {','.join(VARIANTS)}")
    parser.add_argument("--tasks", default="diagnose,propose")
    parser.add_argument("--only", default="", help="Comma-separated case ids")
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--tpm", type=int, default=int(os.environ.get("EVAL_TPM", "7000")),
                        help="Tokens per minute to stay under (Groq free tier: 8000). 0 = no pacing")
    parser.add_argument("--dry-run", action="store_true", help="No API calls: load fixtures, build prompts, baselines")
    parser.add_argument("--resume", action="store_true",
                        help="Keep finished calls from an existing report for this model and only run the rest")
    args = parser.parse_args(argv)

    only = {s for s in args.only.split(",") if s} or None
    fixtures = load_all(args.fixtures, only)
    if not fixtures:
        print(f"no fixtures in {args.fixtures}", file=sys.stderr)
        return 2
    variants = [v for v in args.variants.split(",") if v]
    tasks = [t for t in args.tasks.split(",") if t]
    if set(variants) - set(VARIANTS) or set(tasks) - {"diagnose", "propose"}:
        print("unknown variant or task", file=sys.stderr)
        return 2
    for fx in fixtures:
        if fx.labels["category"] not in CATEGORIES:
            print(f"{fx.id}: unknown category {fx.labels['category']}", file=sys.stderr)
            return 2

    if not os.environ.get("LLM_API_KEY") and os.environ.get("GROQ_API_KEY"):
        os.environ["LLM_API_KEY"] = os.environ["GROQ_API_KEY"]
    from app.config import Settings

    settings = Settings(**({"llm_model": args.model} if args.model else {}))
    model = settings.llm_model
    print(f"{len(fixtures)} fixtures, model={model}, variants={variants}, tasks={tasks}")
    base = baselines(fixtures)
    if args.dry_run:
        from app.actions.proposer import build_context
        from app.llm_explainer import build_user_prompt

        sizes = [len(build_user_prompt(*diagnose_inputs(f, "full"))) for f in fixtures]
        sizes_p = [len(build_context(f.pod, f.events, f.logs, f.deployment, f.signals)) for f in fixtures]
        print(json.dumps({"dataset": dataset_summary(fixtures), "baselines": base,
                          "diagnose_prompt_chars_mean": round(statistics.mean(sizes)),
                          "propose_context_chars_mean": round(statistics.mean(sizes_p))}, indent=2))
        return 0
    if not settings.llm_api_key and not settings.is_ollama():
        print("LLM_API_KEY (or GROQ_API_KEY) is not set", file=sys.stderr)
        return 2

    from openai import OpenAI

    from app.actions.proposer import ActionProposer
    from app.llm_explainer import LLMExplainer

    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "ollama", timeout=120.0,
                    max_retries=0)
    explainer = LLMExplainer(settings=settings, client=client)
    proposer = ActionProposer(settings=settings, client=client)
    pacer = Pacer(args.tpm)
    started = datetime.now(timezone.utc)
    records: list[dict] = []
    stem = args.out_dir / slug(model)
    if only:
        stem = stem.with_name(stem.name + "-subset")
    previous_runs: list[dict] = []
    if args.resume and stem.with_suffix(".json").exists():
        old = json.loads(stem.with_suffix(".json").read_text())
        records = [r for r in old["records"] if "error" not in r and r["case"] in {f.id for f in fixtures}]
        previous_runs = old.get("runs", []) + [{"started_at": old["started_at"], "finished_at": old["finished_at"],
                                                 "status": old["status"], "stopped_reason": old.get("stopped_reason")}]
        started = datetime.fromisoformat(old["started_at"])
        print(f"resuming: {len(records)} finished calls kept")
    done = {(r["case"], r["task"], r["variant"]) for r in records}
    status, stopped = "complete", None
    plan = [(fx, task, v) for v in variants for task in tasks for fx in fixtures
            if runs_variant(fx, task, v) and (fx.id, task, v) not in done]
    print(f"{len(plan)} calls planned")
    try:
        for i, (fx, task, variant) in enumerate(plan, 1):
            fn = run_diagnose if task == "diagnose" else run_propose
            rec = fn(fx, variant, explainer if task == "diagnose" else proposer, pacer)
            records.append(rec)
            mark = rec.get("error") or (
                ("ok" if rec["category"]["correct"] else "WRONG") if task == "diagnose"
                else f"{rec['remediation']['primary']} ({'ok' if rec['remediation']['correct'] else 'WRONG'})")
            print(f"[{i}/{len(plan)}] {task}/{variant} {fx.id}: {mark} {rec.get('latency_s', '')}", flush=True)
    except QuotaExhausted as exc:
        status, stopped = "partial", f"daily quota exhausted after {len(records)} of {len(plan)} calls: {exc}"
        print(stopped, file=sys.stderr)
    except KeyboardInterrupt:
        status, stopped = "partial", f"interrupted after {len(records)} of {len(plan)} calls"

    fixtures_by_id = {f.id: f for f in fixtures}
    report = {
        "model": model,
        "base_url": settings.llm_base_url,
        "status": status,
        "stopped_reason": stopped,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variants": sorted({r["variant"] for r in records}) or variants,
        "tasks": tasks,
        "dataset": dataset_summary(fixtures),
        "baselines": base,
        "pacing": {"tpm": args.tpm, "waited_s": round(pacer.waited_s), "retries": pacer.retries},
        "runs": previous_runs,
        "summary": summarize(records, fixtures_by_id, model),
        "labels": {f.id: f.labels for f in fixtures},
        "records": records,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    stem.with_suffix(".md").write_text(render_markdown(report))
    print(f"wrote {stem}.json and {stem}.md")
    return 0 if status == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
