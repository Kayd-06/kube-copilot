"""Ask the LLM for remediation via function calling. Its output is only a *proposal*."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI, OpenAIError

from app.actions.models import ActionRequest
from app.actions.policy import DeploymentState, PolicyConfig
from app.config import Settings, get_settings
from app.llm_explainer import LLMError
from app.dependencies import dependency_lines
from app.llm_explainer import THROTTLED_HIGH
from app.logcompact import compact_logs
from app.models import DependencyStatus, PodEvent, PodLogs, PodSummary, PodUsage

logger = logging.getLogger(__name__)

NO_ACTION = "no_action"
MAX_PROPOSALS = 3

_TARGET = {
    "namespace": {"type": "string", "description": "Namespace of the target"},
    "name": {"type": "string", "description": "Name of the target"},
}
_REASON = {"reason": {"type": "string", "description": "One or two sentences: why this fixes the incident"}}


def _tool(name: str, description: str, extra: dict | None = None) -> dict:
    properties = {**_TARGET, **(extra or {}), **_REASON}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(properties)},
        },
    }


def build_tools(config: PolicyConfig) -> list[dict]:
    return [
        _tool(
            "rollback_deployment",
            "Roll the Deployment back to its previous ReplicaSet revision (like `kubectl rollout undo`). "
            "Use when the latest rollout introduced the failure (bad image, bad command or config).",
        ),
        _tool(
            "restart_deployment",
            "Rolling restart of the Deployment (like `kubectl rollout restart`). "
            "Use for transient failures that a fresh pod is likely to clear.",
        ),
        _tool(
            "scale_deployment",
            f"Set the Deployment's replica count (allowed range {config.scale_min}-{config.scale_max}). "
            "Use for capacity problems, not for crashing code.",
            {"replicas": {"type": "integer", "description": "Desired replica count"}},
        ),
        _tool(
            "delete_pod",
            "Delete a single crashlooping pod so its ReplicaSet recreates it. "
            "Only for one stuck pod while the rest of the Deployment is healthy.",
        ),
        {
            "type": "function",
            "function": {
                "name": NO_ACTION,
                "description": "None of the allowed actions would fix this; a human must change code or config.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": _REASON["reason"]},
                    "required": ["reason"],
                },
            },
        },
    ]


SYSTEM_PROMPT = """You are a cautious Kubernetes SRE. Propose at most two remediation actions
for the incident below by calling the provided functions. A human reviews and
approves every action, and a policy engine will refuse anything outside the rules.

Rules:
- Only target the incident pod or its Deployment, in the incident namespace.
- Prefer the single least disruptive action that addresses the root cause.
- "Changes in the latest rollout" lists every pod-template field that differs
  between the previous ReplicaSet revision and the current one (image,
  command/args, env and envFrom, ConfigMap/Secret references, probes,
  resources, nodeSelector/affinity, volumes). If the failure is consistent with
  one of those changes (for example a new probe that fails, a changed env value
  the app rejects, a lowered memory or CPU limit, a nodeSelector no node
  matches, a reference to a ConfigMap/Secret/key that does not exist), prefer
  rollback_deployment.
- If the only changes are unrelated to the failure (for example a version label
  or env var), rolling back will not help: the cause is outside the template
  (a ConfigMap/Secret changed or deleted after the rollout, a killed pod, a
  dependency). Do not roll back in that case.
- A rollout that lowered a CPU or memory limit (or request) caused the
  incident when the pod now shows the matching pressure: CPU throttled in a
  large share of periods (see "Resource usage"; roughly 25% or more), OOM kills,
  or work that got slower. This holds even when the pod is Running and Ready and
  nothing crashes. Being Ready is not proof that a resource change was harmless.
  Prefer rollback_deployment to restore the previous resources.
- Dependencies: "Dependencies" says whether each in-cluster Service the pod's
  env points at is serving now. If a dependency is down or missing now, neither
  a rollback nor a restart of this workload helps: call no_action and say which
  dependency to restore.
- restart_deployment and delete_pod do not fix a deterministic bug in the
  current revision; do not propose them for that. They do help when a pod is
  stuck on state that a fresh pod would not have. A typical case: the pod logged
  that a dependency failed at startup, the dependency is serving now (for example
  it recovered after the pod started), and the pod has stayed not-Ready since
  without restarting. Restart it; do not change its probe.
- A healthy pod, or a workload that already replaced a killed pod, needs no action.
- If nothing allowed would help, call no_action and explain what a human should change.
"""

LOG_BUDGET_CHARS = 1500
_CPU = re.compile(r"^(\d+(?:\.\d+)?)(m?)$")
_MEM_UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "": 1}
_MEM = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T|)$")
_RES_DIFF = re.compile(r'resources\.(limits|requests)\.(cpu|memory): "([^"]+)" -> "([^"]+)"')


def _quantity(kind: str, value: str) -> float | None:
    if kind == "cpu":
        m = _CPU.match(value)
        return float(m.group(1)) / (1000 if m.group(2) else 1) if m else None
    m = _MEM.match(value)
    return float(m.group(1)) * _MEM_UNITS[m.group(2)] if m else None


def annotate_diff(line: str) -> str:
    """Say plainly when a rollout lowered or raised a resource, e.g. '(lowered to 6% of the previous value)'."""
    m = _RES_DIFF.search(line)
    if not m:
        return line
    before, after = _quantity(m.group(2), m.group(3)), _quantity(m.group(2), m.group(4))
    if not before or after is None or before == after:
        return line
    ratio = after / before
    word = "LOWERED" if ratio < 1 else "raised"
    return f"{line}  ({word} {m.group(2)} {m.group(1)[:-1]} to {100 * ratio:.0f}% of the previous value)"


def usage_lines(usage: PodUsage | None) -> list[str]:
    out = []
    for c in (usage.containers if usage else []):
        bits = []
        if c.cpu_cores is not None:
            bits.append(f"cpu {c.cpu_cores:.3f} cores" + (f" of a {c.cpu_limit_cores:.3f}-core limit"
                                                          if c.cpu_limit_cores else ""))
        if c.cpu_throttled_ratio is not None:
            high = " HIGH" if c.cpu_throttled_ratio >= THROTTLED_HIGH else ""
            bits.append(f"CPU-throttled in {100 * c.cpu_throttled_ratio:.0f}% of CFS periods{high}")
        if c.memory_mib is not None:
            bits.append(f"memory {c.memory_mib:.1f} MiB" + (f" of {c.memory_limit_mib:.0f} MiB limit"
                                                            if c.memory_limit_mib else ""))
        if bits:
            out.append(f"- {c.name}: " + "; ".join(bits))
    return out


@dataclass
class ProposalResult:
    requests: list[ActionRequest] = field(default_factory=list)
    note: str = ""
    raw_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retried_without_required_tool: bool = False


def build_context(
    pod: PodSummary,
    events: list[PodEvent],
    logs: PodLogs,
    deployment: DeploymentState | None,
    signals: list[str] | None = None,
    usage: PodUsage | None = None,
    dependencies: list[DependencyStatus] | None = None,
) -> str:
    def tail(text: str | None) -> str:
        return compact_logs((text or "").strip(), LOG_BUDGET_CHARS) or "(empty)"

    parts = [
        "## Incident pod",
        json.dumps(
            {
                "namespace": pod.namespace,
                "name": pod.name,
                "phase": pod.phase,
                "reason": pod.reason,
                "message": pod.message,
                "restart_count": pod.restart_count,
                "containers": [c.model_dump() for c in pod.containers],
            },
            indent=2,
            default=str,
        ),
        "## Recent events",
        "\n".join(f"- [{e.type}] {e.reason} x{e.count}: {e.message}" for e in events[-15:]) or "(none)",
        "## Logs (current, tail)",
        tail(logs.current),
        "## Logs (previous container, tail)",
        tail(logs.previous) if logs.previous is not None else "(not fetched)",
    ]
    if deployment is not None:
        parts += [
            "## Owning Deployment",
            json.dumps(
                {
                    "name": deployment.name,
                    "replicas": deployment.replicas,
                    "ready": deployment.ready,
                    "available": deployment.available,
                    "current_revision": deployment.revision,
                    "current_template": deployment.summary.get("template"),
                    "previous_revision": deployment.previous_revision,
                    "previous_template": deployment.previous_summary,
                    "current_references": deployment.template_refs,
                },
                indent=2,
                default=str,
            ),
        ]
        if deployment.previous_revision is not None and deployment.template_diff is not None:
            parts += [
                f"## Changes in the latest rollout (pod template, revision {deployment.previous_revision} -> "
                f"{deployment.revision}; ~ changed, + added, - removed)",
                "\n".join(annotate_diff(line) for line in deployment.template_diff) or "(no pod-template differences)",
            ]
    else:
        parts.append("## Owning Deployment\n(none: the pod is not managed by a Deployment)")
    use = usage_lines(usage)
    if use:
        parts += [f"## Resource usage (Prometheus, last {usage.window})", "\n".join(use)]
    deps = dependency_lines(dependencies, pod.created_at, pod.restart_count)
    if deps:
        parts += ["## Dependencies", "\n".join(deps[1:])]
    if signals:
        parts += ["## Disruption signals (Kubernetes events/lifecycle)", "\n".join(f"- {s}" for s in signals[-15:])]
    return "\n\n".join(parts)


def parse_tool_calls(calls: list[dict[str, Any]]) -> ProposalResult:
    """Turn raw tool calls into ActionRequests. Unknown names are kept so policy can block them."""
    result = ProposalResult(raw_calls=calls)
    for call in calls[: MAX_PROPOSALS + 1]:
        name = str(call.get("name") or "")
        raw_args = call.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise ValueError("arguments are not an object")
        except (ValueError, TypeError):
            result.requests.append(
                ActionRequest(kind=name, params={"_unparsed": str(raw_args)[:200]}, rationale="")
            )
            continue
        reason = str(args.pop("reason", "") or "")
        if name == NO_ACTION:
            result.note = (result.note + " " + reason).strip()
            continue
        result.requests.append(ActionRequest(kind=name, params=args, rationale=reason))
    if len(result.requests) > MAX_PROPOSALS:
        result.requests = result.requests[:MAX_PROPOSALS]
    return result


class ActionProposer:
    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> OpenAI:
        if self._client is None:
            key = self._settings.llm_api_key or ("ollama" if self._settings.is_ollama() else "")
            if not key:
                raise LLMError("LLM_API_KEY is not set; cannot propose actions.")
            self._client = OpenAI(base_url=self._settings.llm_base_url, api_key=key, timeout=60.0)
        return self._client

    def propose(self, context: str, config: PolicyConfig) -> ProposalResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]
        kwargs = dict(
            model=self._settings.llm_model,
            messages=messages,
            tools=build_tools(config),
            tool_choice="required",
            temperature=0.0,
        )
        retried = False
        try:
            response = self._get_client().chat.completions.create(**kwargs)
        except OpenAIError as exc:
            # Some models (seen with gpt-oss-20b on Groq) occasionally answer in prose and the provider
            # rejects the reply with "Tool choice is required". Retry once letting the model choose; a
            # prose-only answer then becomes a no-action note below.
            if "tool choice is required" not in str(exc).lower() and "tool_use_failed" not in str(exc).lower():
                raise LLMError(f"LLM tool call failed: {exc}") from exc
            logger.info("Model did not call a tool; retrying once with tool_choice=auto")
            retried = True
            try:
                response = self._get_client().chat.completions.create(**{**kwargs, "tool_choice": "auto"})
            except OpenAIError as retry_exc:
                raise LLMError(f"LLM tool call failed: {retry_exc}") from retry_exc
        message = response.choices[0].message if response.choices else None
        calls = [
            {"name": tc.function.name, "arguments": tc.function.arguments}
            for tc in ((message.tool_calls or []) if message is not None else [])
        ]
        result = parse_tool_calls(calls)
        result.retried_without_required_tool = retried
        if not calls and message is not None and message.content:
            result.note = message.content.strip()[:1000]
        usage = getattr(response, "usage", None)
        if usage is not None:
            result.prompt_tokens = getattr(usage, "prompt_tokens", None)
            result.completion_tokens = getattr(usage, "completion_tokens", None)
        return result
