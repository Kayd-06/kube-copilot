"""Ask the LLM for remediation via function calling. Its output is only a *proposal*."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI, OpenAIError

from app.actions.models import ActionRequest
from app.actions.policy import DeploymentState, PolicyConfig
from app.config import Settings, get_settings
from app.llm_explainer import LLMError
from app.models import PodEvent, PodLogs, PodSummary

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
- If the current revision is broken and the previous revision looks healthy
  (different image/command), prefer rollback_deployment.
- restart_deployment and delete_pod do not fix a deterministic bug in the
  current revision; do not propose them for that.
- If nothing allowed would help, call no_action and explain what a human should change.
"""


@dataclass
class ProposalResult:
    requests: list[ActionRequest] = field(default_factory=list)
    note: str = ""
    raw_calls: list[dict[str, Any]] = field(default_factory=list)


def build_context(
    pod: PodSummary,
    events: list[PodEvent],
    logs: PodLogs,
    deployment: DeploymentState | None,
    signals: list[str] | None = None,
) -> str:
    def tail(text: str | None, n: int = 30) -> str:
        lines = (text or "").strip().splitlines()
        return "\n".join(lines[-n:]) or "(empty)"

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
                },
                indent=2,
                default=str,
            ),
        ]
    else:
        parts.append("## Owning Deployment\n(none: the pod is not managed by a Deployment)")
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
        try:
            response = self._get_client().chat.completions.create(
                model=self._settings.llm_model,
                messages=messages,
                tools=build_tools(config),
                tool_choice="required",
                temperature=0.0,
            )
        except OpenAIError as exc:
            raise LLMError(f"LLM tool call failed: {exc}") from exc
        message = response.choices[0].message if response.choices else None
        calls = [
            {"name": tc.function.name, "arguments": tc.function.arguments}
            for tc in ((message.tool_calls or []) if message is not None else [])
        ]
        result = parse_tool_calls(calls)
        if not calls and message is not None and message.content:
            result.note = message.content.strip()[:1000]
        return result
