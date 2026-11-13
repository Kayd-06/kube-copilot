"""Deterministic scoring: schema validity, root-cause category, remediation, retrieval, hallucination."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from ml.llm_eval.taxonomy import CATEGORIES, PATTERNS, matches, predict_category

DIAGNOSIS_KEYS = {
    "summary": str,
    "likely_root_cause": str,
    "suggested_fix": str,
    "kubectl_commands": list,
    "confidence": (int, float),
}


# ------------------------------------------------------------------ schema
def strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned


def schema_check(raw_text: str) -> tuple[bool, str]:
    """Strict: one JSON object with exactly the five keys and the right types (fences tolerated)."""
    try:
        data = json.loads(strip_fences(raw_text))
    except (json.JSONDecodeError, TypeError) as exc:
        return False, f"not JSON: {exc}"
    if not isinstance(data, dict):
        return False, "not an object"
    missing = [k for k in DIAGNOSIS_KEYS if k not in data]
    if missing:
        return False, f"missing keys: {missing}"
    for key, typ in DIAGNOSIS_KEYS.items():
        value = data[key]
        if not isinstance(value, typ) or isinstance(value, bool):
            return False, f"{key} has type {type(value).__name__}"
    if not all(isinstance(c, str) for c in data["kubectl_commands"]):
        return False, "kubectl_commands has non-string items"
    if not 0.0 <= float(data["confidence"]) <= 1.0:
        return False, "confidence outside [0, 1]"
    extra = sorted(set(data) - set(DIAGNOSIS_KEYS))
    return True, f"extra keys: {extra}" if extra else ""


# ------------------------------------------------------------------ category
def score_category(expected: str, root_cause: str) -> dict[str, Any]:
    if expected not in CATEGORIES:
        raise ValueError(f"unknown category {expected}")
    return {
        "correct": matches(expected, root_cause),
        "predicted": predict_category(root_cause),
        "also_matched": sorted(name for name, p in PATTERNS.items() if p.search(root_cause or "")),
    }


# ------------------------------------------------------------------ remediation
def primary_action(kinds: list[str]) -> str:
    return kinds[0] if kinds else "no_action"


def score_remediation(
    expected: list[str], kinds: list[str], targets: list[dict], namespace: str, deployment: str | None, pod: str
) -> dict[str, Any]:
    primary = primary_action(kinds)
    target_errors = []
    for kind, params in zip(kinds, targets):
        want = pod if kind == "delete_pod" else deployment
        if params.get("namespace") != namespace or params.get("name") != want:
            target_errors.append(f"{kind} -> {params.get('namespace')}/{params.get('name')} (expected {namespace}/{want})")
    return {
        "primary": primary,
        "correct": primary in expected,
        "all_expected": all(k in expected for k in (kinds or ["no_action"])),
        "target_errors": target_errors,
    }


# ------------------------------------------------------------------ retrieval
def score_retrieval(expected_sources: list[str], retrieved_sources: list[str]) -> dict[str, Any] | None:
    if not expected_sources:
        return None
    rank = next((i + 1 for i, s in enumerate(retrieved_sources) if s in expected_sources), None)
    return {"hit": rank is not None, "hit_at_1": rank == 1, "rank": rank, "rr": 1.0 / rank if rank else 0.0}


# ------------------------------------------------------------------ hallucination
RESOURCE_TYPES = {
    "pod", "pods", "po", "deployment", "deployments", "deploy", "replicaset", "replicasets", "rs", "service",
    "services", "svc", "configmap", "configmaps", "cm", "secret", "secrets", "pvc", "persistentvolumeclaim",
    "persistentvolumeclaims", "pv", "persistentvolume", "node", "nodes", "no", "namespace", "namespaces", "ns",
    "endpoints", "ep", "endpointslice", "endpointslices", "event", "events", "ev", "job", "jobs", "statefulset",
    "statefulsets", "sts", "daemonset", "daemonsets", "ds", "storageclass", "storageclasses", "sc", "ingress",
    "hpa", "horizontalpodautoscaler", "networkpolicy", "networkpolicies", "serviceaccount", "sa", "cronjob",
    "cronjobs", "resourcequota", "limitrange", "podchaos",
}
NAME_VERBS = {"get", "describe", "delete", "edit", "patch", "label", "annotate", "scale", "autoscale", "top", "set"}
POD_VERBS = {"logs", "exec", "port-forward", "attach", "cp", "debug"}
_PLACEHOLDER = re.compile(r"[<>${}]|^[A-Z_]{3,}$|\b(your|my|example)-")
FLAGS_WITH_VALUE = {
    "-n", "--namespace", "-o", "--output", "-l", "--selector", "-c", "--container", "--tail", "--since",
    "--field-selector", "--sort-by", "--replicas", "--type", "-p", "--patch", "--to-revision", "--timeout", "-f",
    "--image", "--from-literal", "--from-file", "--context", "-L", "--columns",
}


def _tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def kubectl_refs(cmd: str) -> dict[str, list[str]]:
    """Namespaces and resource names a kubectl command refers to."""
    toks = _tokens(cmd)
    if "kubectl" not in toks:
        return {"namespaces": [], "names": []}
    toks = toks[toks.index("kubectl") + 1 :]
    namespaces: list[str] = []
    positional: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("|", "&&", ";", "||"):
            break
        if t in ("-n", "--namespace") and i + 1 < len(toks):
            namespaces.append(toks[i + 1])
            i += 2
            continue
        if t.startswith("--namespace="):
            namespaces.append(t.split("=", 1)[1])
        elif t.startswith("-n") and len(t) > 2 and not t.startswith("--"):
            namespaces.append(t[2:])
        elif t.startswith("--field-selector") or (i > 0 and toks[i - 1] == "--field-selector"):
            value = t.split("=", 1)[1] if t.startswith("--field-selector=") else t
            for part in value.split(","):
                if part.startswith("involvedObject.name="):
                    positional.append("@" + part.split("=", 1)[1])
        elif t in FLAGS_WITH_VALUE:
            i += 2
            continue
        elif not t.startswith("-"):
            positional.append(t)
        i += 1

    names: list[str] = [p[1:] for p in positional if p.startswith("@")]
    positional = [p for p in positional if not p.startswith("@")]
    if not positional:
        return {"namespaces": namespaces, "names": names}
    verb, rest = positional[0], positional[1:]
    if verb == "rollout" and rest:
        rest = rest[1:]  # undo/restart/status/history
    elif verb == "set" and rest:
        rest = rest[1:]  # image/env/resources
    for tok in rest:
        if "/" in tok and not tok.startswith(("/", "http")):
            rtype, _, name = tok.partition("/")
            if rtype.lower() in RESOURCE_TYPES and name:
                names.append(name)
    if verb in POD_VERBS and rest and "/" not in rest[0]:
        names.append(rest[0])
    elif (verb in NAME_VERBS or verb == "rollout" or positional[0] == "set") and len(rest) >= 2:
        if rest[0].lower() in RESOURCE_TYPES and "/" not in rest[1] and "=" not in rest[1]:
            if verb != "set":
                names.append(rest[1])
    return {"namespaces": namespaces, "names": names}


# Objects every cluster has; referring to them is not inventing anything.
WELL_KNOWN = {"kube-system", "default", "kube-public", "kube-node-lease", "coredns", "kube-dns", "kube-proxy"}


def score_hallucination(commands: list[str], context_text: str) -> dict[str, Any]:
    unknown: list[str] = []
    placeholders: list[str] = []
    checked = 0
    for cmd in commands:
        refs = kubectl_refs(cmd)
        for kind, values in refs.items():
            for value in values:
                value = value.strip("'\"")
                if not value:
                    continue
                if _PLACEHOLDER.search(value):
                    placeholders.append(value)
                    continue
                checked += 1
                if value not in context_text and value not in WELL_KNOWN:
                    unknown.append(f"{kind[:-1]}:{value}")
    return {"checked_refs": checked, "unknown_refs": unknown, "placeholders": placeholders, "hallucinated": bool(unknown)}


# ------------------------------------------------------------------ baselines (no LLM)
REASON_TABLE = [
    (re.compile(r"ErrImagePull|ImagePullBackOff|InvalidImageName", re.I), "image_pull"),
    (re.compile(r"OOMKilled", re.I), "oom_killed"),
    (re.compile(r"CreateContainerConfigError", re.I), "missing_config"),
    (re.compile(r"Unschedulable|FailedScheduling", re.I), "unschedulable"),
    (re.compile(r"CrashLoopBackOff|Error|RunContainerError|StartError|CreateContainerError", re.I), "app_crash"),
]


def reason_lookup_category(pod: dict) -> str:
    """A lookup table on pod/container reasons: what you get without any model."""
    reasons = [pod.get("reason") or ""] + [
        f"{c.get('reason') or ''} {c.get('state') or ''}" for c in pod.get("containers") or []
    ]
    blob = " ".join(reasons)
    for pattern, category in REASON_TABLE:
        if pattern.search(blob):
            return category
    if pod.get("phase") == "Pending":
        return "unschedulable"
    return "healthy"
