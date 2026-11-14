"""Build a retrieval query from pod evidence and search runbooks plus incidents."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.models import PodEvent, PodLogs, PodSummary, PodUsage
from app.rag.store import (
    KIND_INCIDENT,
    KIND_RUNBOOK,
    KnowledgeStore,
    KnowledgeUnavailableError,
    ScoredChunk,
)

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    title: str
    source: str
    section: str
    text: str
    score: float
    kind: str = KIND_RUNBOOK

    def to_citation(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "section": self.section,
            "score": round(self.score, 4),
            "kind": self.kind,
        }


@dataclass
class RetrieveResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    query: str = ""
    knowledge_unavailable: bool = False
    warning: str | None = None


THROTTLING_QUERY_RATIO = 0.25


def build_retrieval_query(
    pod: PodSummary, events: list[PodEvent], logs: PodLogs, usage: PodUsage | None = None
) -> str:
    """Compact lexical query from status, events, resource usage and a short log tail."""
    parts: list[str] = []
    for c in (usage.containers if usage is not None else []):
        if (c.cpu_throttled_ratio or 0) >= THROTTLING_QUERY_RATIO:
            parts.append(f"CPU throttling: container {c.name} throttled at its CPU limit, slow responses")
    if pod.phase:
        parts.append(pod.phase)
    if pod.reason:
        parts.append(pod.reason)
    if pod.message:
        parts.append(pod.message)
    for container in pod.containers:
        if container.reason:
            parts.append(container.reason)
        if container.message:
            parts.append(container.message)
        if container.exit_code is not None:
            parts.append(f"exit code {container.exit_code}")
    # Newest events carry the most diagnostic signal; take a handful.
    for event in events[-8:]:
        if event.reason:
            parts.append(event.reason)
        if event.message:
            parts.append(event.message)

    log_blob = "\n".join(filter(None, [logs.previous or "", logs.current or ""]))
    log_lines = [line.strip() for line in log_blob.splitlines() if line.strip()]
    # Skip the generic "logs unavailable" placeholder — it only adds noise.
    useful_lines = [line for line in log_lines if not line.startswith("(logs unavailable")]
    parts.extend(useful_lines[-30:])

    query = " ".join(part for part in parts if part)
    if len(query) > 4000:
        query = query[:4000]
    return query


def _from_scored(chunk: ScoredChunk) -> RetrievedChunk:
    return RetrievedChunk(
        title=chunk.title,
        source=chunk.source,
        section=chunk.section,
        text=chunk.text,
        score=chunk.score,
        kind=chunk.kind or KIND_RUNBOOK,
    )


class Retriever:
    def __init__(
        self,
        store: KnowledgeStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._store = store

    def store(self) -> KnowledgeStore:
        if self._store is None:
            self._store = KnowledgeStore(settings=self._settings)
        return self._store

    def retrieve_for_pod(
        self,
        pod: PodSummary,
        events: list[PodEvent],
        logs: PodLogs,
        top_k: int | None = None,
        usage: PodUsage | None = None,
    ) -> RetrieveResult:
        query = build_retrieval_query(pod, events, logs, usage)
        return self.search(query, top_k=top_k)

    def search(self, query: str, top_k: int | None = None) -> RetrieveResult:
        if not self._settings.rag_enabled:
            return RetrieveResult(
                query=query,
                knowledge_unavailable=True,
                warning="RAG is disabled (RAG_ENABLED=false).",
            )
        store = self.store()
        if not store.is_available():
            return RetrieveResult(
                query=query,
                knowledge_unavailable=True,
                warning="Knowledge store is unavailable; diagnosing without runbooks.",
            )
        limit = top_k if top_k is not None else self._settings.rag_top_k
        # Fetch a few extra from each collection, then merge by score.
        per_collection = max(limit, 3)
        try:
            runbooks = store.search(store.runbooks_collection, query, top_k=per_collection)
            incidents = store.search(store.incidents_collection, query, top_k=per_collection)
        except KnowledgeUnavailableError as exc:
            logger.warning("Retrieval failed: %s", exc)
            return RetrieveResult(
                query=query,
                knowledge_unavailable=True,
                warning=f"Knowledge store is unavailable; diagnosing without runbooks. ({exc})",
            )

        merged = [_from_scored(hit) for hit in runbooks]
        merged.extend(_from_scored(hit) for hit in incidents)
        # Prefer higher scores; if equal, prefer runbooks for stability.
        kind_rank = {KIND_RUNBOOK: 0, KIND_INCIDENT: 1}
        merged.sort(key=lambda item: (-item.score, kind_rank.get(item.kind, 2), item.source))
        deduped: list[RetrievedChunk] = []
        seen: set[tuple[str, str, str]] = set()
        for item in merged:
            key = (item.source, item.section, item.kind)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return RetrieveResult(chunks=deduped, query=query, knowledge_unavailable=False)

    def save_incident(
        self,
        *,
        pod_name: str,
        namespace: str,
        reason: str | None,
        root_cause: str,
        fix: str,
        notes: str | None = None,
    ) -> str:
        store = self.store()
        if not store.is_available():
            raise KnowledgeUnavailableError(
                "Knowledge store is unavailable; cannot save the incident."
            )
        title = f"{namespace}/{pod_name}"
        section = reason or "incident"
        lines = [
            f"# Incident: {title}",
            f"Reason: {reason or 'n/a'}",
            f"Root cause: {root_cause}",
            f"Fix: {fix}",
        ]
        if notes:
            lines.append(f"Notes: {notes}")
        text = "\n".join(lines)
        return store.upsert_payload(
            store.incidents_collection,
            text=text,
            title=title,
            source="incident-memory",
            section=section,
            kind=KIND_INCIDENT,
            extra={
                "pod_name": pod_name,
                "namespace": namespace,
                "reason": reason or "",
                "root_cause": root_cause,
                "fix": fix,
            },
        )
