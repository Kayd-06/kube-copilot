"""Build a diagnosis prompt from pod evidence and call an OpenAI-compatible LLM."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from openai import APIError, OpenAI, OpenAIError

from app.config import Settings, get_settings
from app.logcompact import compact_logs
from app.models import AnomalyScore, Diagnosis, PodEvent, PodLogs, PodSummary, PodUsage
from app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# Per-log character budget (~1K tokens each). Keeps a diagnose under Groq's
# 8K tokens-per-minute free tier even with chatty logs.
LOG_BUDGET_CHARS = 3000
THROTTLED_HIGH = 0.25
# Lifecycle signal kinds (ml/lifecycle.py, "event:" prefix dropped) that mean a pod was stopped or replaced
# from outside, as opposed to crash signals like BackOff / container_restarted.
DISRUPTIVE_SIGNALS = frozenset({
    "Killing", "pod_replaced", "pod_vanished", "Evicted", "Preempting", "TaintManagerEviction",
    "replicas_unavailable", "SuccessfulDelete", "SuccessfulCreate", "NodeNotReady",
})
# Precision of the metrics-only Isolation Forest on the held-out chaos run (ml/reports/lifecycle_metrics.md).
ANOMALY_CAVEAT = (
    "weak evidence: this model is not calibrated and also flags healthy pods (only about half of its "
    "alerts were real on a held-out chaos run). Do not use it as a root cause unless other evidence agrees."
)

SYSTEM_PROMPT = """You are a senior Kubernetes SRE helping diagnose a failing pod.

You will receive the pod's status (including each container's resource requests
and limits), recent cluster events, container logs, and possibly: resource usage
from Prometheus (CPU use, CPU throttling, memory vs limits), lifecycle signals
for the pod's workload (from Kubernetes events and ReplicaSet state), retrieved
runbooks and past incidents, and an anomaly score from a metrics model.

How to weigh the evidence:
1. Pod status, container states and exit codes, events and logs are the primary
   evidence. Do not invent facts that are not supported by the evidence or the
   retrieved knowledge.
2. Workload lifecycle signals are factual. If they show pods of this workload
   being killed, deleted, evicted or replaced (with no rollout or scale-up
   explaining it) while the pod you are looking at is itself healthy, the root
   cause is a pod disruption: something external killed or removed a pod (chaos
   testing, a node drain, eviction or a manual delete) and the ReplicaSet
   replaced it. Say so explicitly and name the replaced pod.
3. Resource usage: compare usage with the requests and limits. A container
   throttled in a large share of its CFS periods (roughly 25% or more) while its
   CPU use sits at its CPU limit is CPU-throttled: the limit is too low for the
   load, even when nothing crashes and the logs look normal.
4. The anomaly score is weak, uncalibrated evidence: the model also flags many
   healthy pods. Never name a root cause from the anomaly score alone; it needs
   corroboration from status, events, logs, lifecycle signals or resource usage.
   If the pod is Running/Ready (or Succeeded), has no restarts, no warning events,
   no errors in its logs and no disruption or throttling, conclude that it is
   healthy and the anomaly flag is most likely a false positive.
5. Log warnings on a Ready pod with no restarts are usually routine (retries
   that succeed, cache stats). Only treat them as the root cause when they show
   real failures that match the pod's state.

Respond with a single JSON object (no markdown fences) with exactly these keys:
- "summary": two or three sentences describing what is happening to the pod.
- "likely_root_cause": the most probable root cause, specific and concise.
- "suggested_fix": practical remediation steps the operator should take.
- "kubectl_commands": an array of exact kubectl commands to inspect or fix the issue.
  Use the real pod name and namespace from the evidence. Prefer read-only
  investigative commands first.
- "confidence": a number between 0 and 1 reflecting how sure you are,
  given the evidence (0.3 = weak/incomplete logs, 0.9 = clear smoking gun).

If the evidence is thin, say so, lower confidence, and still suggest next
commands that would gather the missing signal.
"""


class LLMError(Exception):
    """Raised when the LLM call fails or cannot be configured."""


@dataclass
class DiagnosisRun:
    """One non-streaming diagnosis with what an evaluation needs to score it."""

    diagnosis: Diagnosis
    raw_text: str
    parsed: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def parse_diagnosis(text: str) -> Diagnosis:
    """Parse a Diagnosis from model output, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # If the model wrapped JSON in prose, take the outermost object.
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Model output JSON was not an object")

    commands = data.get("kubectl_commands") or []
    if isinstance(commands, str):
        commands = [line.strip() for line in commands.splitlines() if line.strip()]
    if not isinstance(commands, list):
        commands = [str(commands)]
    commands = [str(c).strip() for c in commands if str(c).strip()]

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return Diagnosis(
        summary=str(data.get("summary") or "").strip() or "No summary was returned.",
        likely_root_cause=str(data.get("likely_root_cause") or "").strip()
        or "Unknown — the model did not provide a root cause.",
        suggested_fix=str(data.get("suggested_fix") or "").strip()
        or "Inspect the pod with kubectl describe and kubectl logs, then retry.",
        kubectl_commands=commands,
        confidence=confidence,
    )


def fallback_diagnosis(raw_text: str, pod: PodSummary) -> Diagnosis:
    """Used when the model returns text that is not valid diagnosis JSON."""
    excerpt = (raw_text or "").strip()
    if len(excerpt) > 2000:
        excerpt = excerpt[:2000] + "…"
    return Diagnosis(
        summary=excerpt or "The model returned an unparseable response.",
        likely_root_cause="Unknown — the model response was not valid JSON.",
        suggested_fix=(
            "Retry the diagnosis, or inspect the pod manually with the "
            "commands below."
        ),
        kubectl_commands=[
            f"kubectl describe pod {pod.name} -n {pod.namespace}",
            f"kubectl logs {pod.name} -n {pod.namespace} --tail=200",
            f"kubectl logs {pod.name} -n {pod.namespace} --previous --tail=200",
            f"kubectl get events -n {pod.namespace} --field-selector involvedObject.name={pod.name}",
        ],
        confidence=0.0,
    )


def _usage_lines(usage: PodUsage | None) -> list[str]:
    if usage is None or not usage.containers:
        return []
    out = [f"Resource usage (Prometheus, last {usage.window}):"]
    for c in usage.containers:
        bits = []
        if c.cpu_cores is not None:
            cpu = f"cpu {c.cpu_cores:.3f} cores"
            if c.cpu_limit_cores:
                cpu += f" of a {c.cpu_limit_cores:.3f}-core limit ({100 * c.cpu_cores / c.cpu_limit_cores:.0f}% of limit)"
            elif c.cpu_request_cores:
                cpu += f" (request {c.cpu_request_cores:.3f}, no CPU limit)"
            else:
                cpu += " (no CPU limit)"
            bits.append(cpu)
        if c.cpu_throttled_ratio is not None:
            flag = " HIGH" if c.cpu_throttled_ratio >= THROTTLED_HIGH else ""
            bits.append(f"CPU-throttled in {100 * c.cpu_throttled_ratio:.0f}% of CFS periods{flag}")
        if c.memory_mib is not None:
            mem = f"memory {c.memory_mib:.1f} MiB"
            if c.memory_limit_mib:
                mem += f" of {c.memory_limit_mib:.0f} MiB limit"
            bits.append(mem)
        out.append(f"- {c.name}: " + "; ".join(bits))
    return out


def _lifecycle_lines(anomaly: AnomalyScore | None) -> list[str]:
    signals = anomaly.lifecycle_signals if anomaly is not None else []
    if not signals:
        return []
    counts: dict[str, int] = {}
    for sig in signals:
        key = sig.kind.removeprefix("event:")
        counts[key] = counts.get(key, 0) + 1
    hit = sorted(k for k in counts if k in DISRUPTIVE_SIGNALS)
    out = [
        f"Workload lifecycle signals for {signals[0].workload} (Kubernetes events and ReplicaSet state; "
        "factual, most recent last):",
        "- counts: " + ", ".join(f"{k} x{n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])),
    ]
    if hit:
        out.append(f"- disruption signals present: {', '.join(hit)} (pods of this workload were stopped/replaced)")
    for sig in signals:
        pod = f" pod={sig.pod}" if sig.pod else ""
        out.append(f"- {sig.at.isoformat()} {sig.kind}{pod} {sig.detail}".rstrip())
    return out


def build_user_prompt(
    pod: PodSummary,
    events: list[PodEvent],
    logs: PodLogs,
    retrieved: list[RetrievedChunk] | None = None,
    anomaly: AnomalyScore | None = None,
    usage: PodUsage | None = None,
) -> str:
    containers = []
    for c in pod.containers:
        entry = {
            "name": c.name,
            "ready": c.ready,
            "restart_count": c.restart_count,
            "state": c.state,
            "reason": c.reason,
            "message": c.message,
            "exit_code": c.exit_code,
        }
        if c.resources:
            entry["resources"] = c.resources
        containers.append(entry)

    event_lines = []
    for ev in events:
        ts = ev.last_timestamp.isoformat() if ev.last_timestamp else "?"
        event_lines.append(
            f"- [{ev.type}] {ev.reason} x{ev.count} @ {ts}: {ev.message}"
        )

    current_logs = compact_logs(logs.current, LOG_BUDGET_CHARS) or "(no current logs)"
    previous_logs = (
        compact_logs(logs.previous, LOG_BUDGET_CHARS) if logs.previous is not None else "(not fetched — no restarts)"
    )

    parts = [
        f"Pod: {pod.namespace}/{pod.name}",
        f"Phase: {pod.phase}",
        f"Reported reason: {pod.reason or 'n/a'}",
        f"Message: {pod.message or 'n/a'}",
        f"Restarts: {pod.restart_count}",
        f"Ready: {pod.ready}",
        f"Node: {pod.node or 'n/a'}",
        f"QoS class: {pod.qos_class or 'n/a'}",
        "Containers:",
        json.dumps(containers, indent=2),
        "",
    ]
    usage_part = _usage_lines(usage)
    if usage_part:
        parts += usage_part + [""]
    lifecycle_part = _lifecycle_lines(anomaly)
    if lifecycle_part:
        parts += lifecycle_part + [""]
    parts += [
        "Recent events (oldest first):",
        "\n".join(event_lines) if event_lines else "(no events found)",
        "",
        f"Current logs (container={logs.container or 'default'}, last lines):",
        current_logs,
        "",
        "Previous container logs (from the last restart):",
        previous_logs,
    ]
    if retrieved:
        parts.append("")
        parts.append(
            "Retrieved knowledge (runbooks and past incidents). "
            "Use these when they match the evidence. Do not invent citations."
        )
        for index, chunk in enumerate(retrieved, start=1):
            parts.append(
                f"[{index}] ({chunk.kind}) {chunk.title} — {chunk.section} "
                f"(source: {chunk.source})"
            )
            parts.append(chunk.text)
            parts.append("")
    if anomaly is not None:
        parts.append(f"Anomaly detector (Isolation Forest on recent Prometheus metrics) — {ANOMALY_CAVEAT}")
        metric_flag = anomaly.is_anomaly if anomaly.metric_anomaly is None else anomaly.metric_anomaly
        parts.append(
            f"- score: {anomaly.score:.3f} (0.5 is the decision boundary); flagged: {str(metric_flag).lower()}"
        )
        if anomaly.contributing_features:
            feats = ", ".join(
                f"{f.name}={f.value:.3f} ({f.direction}, z={f.zscore:.1f})" for f in anomaly.contributing_features
            )
            parts.append(f"- top features vs the healthy training data: {feats}")
        if anomaly.warning:
            parts.append(f"- warning: {anomaly.warning}")
        parts.append("")
    return "\n".join(parts)


class LLMExplainer:
    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        api_key = self._settings.llm_api_key
        if not api_key:
            # Ollama accepts any non-empty key; hosted providers need a real one.
            if self._settings.is_ollama():
                api_key = "ollama"
            else:
                raise LLMError(
                    "LLM_API_KEY is not set. Add it to your .env file. "
                    "For Groq, create a free key at https://console.groq.com/keys. "
                    "OpenAI and other OpenAI-compatible endpoints also work."
                )
        self._client = OpenAI(
            base_url=self._settings.llm_base_url,
            api_key=api_key,
            timeout=60.0,
        )
        return self._client

    def _supports_json_response_format(self) -> bool:
        # Ollama's OpenAI-compatible server often rejects response_format.
        return not self._settings.is_ollama()

    def _create_kwargs(self, messages: list[dict], stream: bool = False) -> dict:
        kwargs: dict = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": 0.2,
            "stream": stream,
        }
        if self._supports_json_response_format():
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def explain(
        self,
        pod: PodSummary,
        events: list[PodEvent],
        logs: PodLogs,
        retrieved: list[RetrievedChunk] | None = None,
        anomaly: AnomalyScore | None = None,
        usage: PodUsage | None = None,
    ) -> Diagnosis:
        return self.explain_detailed(pod, events, logs, retrieved, anomaly, usage).diagnosis

    def explain_detailed(
        self,
        pod: PodSummary,
        events: list[PodEvent],
        logs: PodLogs,
        retrieved: list[RetrievedChunk] | None = None,
        anomaly: AnomalyScore | None = None,
        usage: PodUsage | None = None,
    ) -> DiagnosisRun:
        """Like explain(), but also returns the raw text, parse status and token usage (used by evals)."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(pod, events, logs, retrieved, anomaly, usage)},
        ]
        client = self._get_client()
        try:
            response = client.chat.completions.create(**self._create_kwargs(messages, stream=False))
        except (APIError, OpenAIError, Exception) as exc:
            # Retry once without response_format if the provider rejected it.
            if "response_format" in str(exc).lower() or "json_object" in str(exc).lower():
                logger.info("Provider rejected response_format; retrying without it")
                kwargs = self._create_kwargs(messages, stream=False)
                kwargs.pop("response_format", None)
                try:
                    response = client.chat.completions.create(**kwargs)
                except (APIError, OpenAIError) as retry_exc:
                    raise LLMError(f"LLM request failed: {retry_exc}") from retry_exc
            elif isinstance(exc, (APIError, OpenAIError)):
                raise LLMError(f"LLM request failed: {exc}") from exc
            else:
                raise LLMError(f"LLM request failed: {exc}") from exc

        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""
        token_usage = getattr(response, "usage", None)
        prompt_tokens = getattr(token_usage, "prompt_tokens", None) if token_usage is not None else None
        completion_tokens = getattr(token_usage, "completion_tokens", None) if token_usage is not None else None
        try:
            diagnosis, parsed = parse_diagnosis(text), True
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Failed to parse diagnosis JSON: %s", exc)
            diagnosis, parsed = fallback_diagnosis(text, pod), False
        return DiagnosisRun(
            diagnosis=diagnosis,
            raw_text=text,
            parsed=parsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def explain_stream(
        self,
        pod: PodSummary,
        events: list[PodEvent],
        logs: PodLogs,
        retrieved: list[RetrievedChunk] | None = None,
        anomaly: AnomalyScore | None = None,
        usage: PodUsage | None = None,
    ) -> Iterator[str]:
        """Yield text chunks from the model, then the caller parses the full text."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(pod, events, logs, retrieved, anomaly, usage)},
        ]
        client = self._get_client()
        kwargs = self._create_kwargs(messages, stream=True)
        try:
            stream = client.chat.completions.create(**kwargs)
        except (APIError, OpenAIError, Exception) as exc:
            if "response_format" in str(exc).lower() or "json_object" in str(exc).lower():
                kwargs.pop("response_format", None)
                try:
                    stream = client.chat.completions.create(**kwargs)
                except (APIError, OpenAIError) as retry_exc:
                    raise LLMError(f"LLM request failed: {retry_exc}") from retry_exc
            elif isinstance(exc, (APIError, OpenAIError)):
                raise LLMError(f"LLM request failed: {exc}") from exc
            else:
                raise LLMError(f"LLM request failed: {exc}") from exc

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None) or ""
            if piece:
                yield piece

    async def aexplain_stream(
        self,
        pod: PodSummary,
        events: list[PodEvent],
        logs: PodLogs,
        retrieved: list[RetrievedChunk] | None = None,
        anomaly: AnomalyScore | None = None,
        usage: PodUsage | None = None,
    ) -> AsyncIterator[str]:
        # The official OpenAI SDK streaming iterator is sync; wrap it for SSE.
        for piece in self.explain_stream(pod, events, logs, retrieved, anomaly, usage):
            yield piece
