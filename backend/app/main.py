"""FastAPI entrypoint for Kube Copilot."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import __version__
from app.config import Settings, get_settings
from app.deps import get_k8s_client, get_llm_explainer, get_retriever
from app.k8s_client import K8sClient, K8sUnavailableError, PodNotFoundError
from app.llm_explainer import LLMError, LLMExplainer, fallback_diagnosis, parse_diagnosis
from app.models import (
    Citation,
    DiagnoseResponse,
    Diagnosis,
    HealthResponse,
    IncidentCreate,
    IncidentSaved,
    KnowledgeHit,
    KnowledgeSearchResponse,
    UnhealthyPodsResponse,
)
from app.rag.retriever import RetrievedChunk, Retriever, RetrieveResult
from app.rag.store import KnowledgeUnavailableError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(
        title="Kube Copilot",
        description=(
            "AI incident copilot for Kubernetes — Phase 2 diagnoses unhealthy "
            "pods with an LLM, citing retrieved runbooks and past incidents."
        ),
        version=__version__,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return application


app = create_app()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@app.get("/pods/unhealthy", response_model=UnhealthyPodsResponse)
def list_unhealthy_pods(
    namespace: str | None = Query(default=None, description="Limit the scan to one namespace"),
    k8s: K8sClient = Depends(get_k8s_client),
) -> UnhealthyPodsResponse:
    try:
        pods = k8s.list_unhealthy_pods(namespace=namespace)
    except K8sUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return UnhealthyPodsResponse(pods=pods, count=len(pods))


def _collect_evidence(k8s: K8sClient, namespace: str, name: str):
    try:
        raw_pod = k8s.get_pod(namespace, name)
    except PodNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except K8sUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    from app.k8s_client import to_pod_summary

    pod = to_pod_summary(raw_pod)
    try:
        events = k8s.get_pod_events(namespace, name)
        logs = k8s.get_pod_logs(namespace, name, pod=raw_pod)
    except K8sUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return pod, events, logs


def _citations(chunks: list[RetrievedChunk]) -> list[Citation]:
    return [
        Citation(
            title=chunk.title,
            source=chunk.source,
            section=chunk.section,
            score=round(float(chunk.score), 4),
            kind=chunk.kind,
        )
        for chunk in chunks
    ]


def _retrieve(retriever: Retriever, pod, events, logs) -> RetrieveResult:
    try:
        return retriever.retrieve_for_pod(pod, events, logs)
    except KnowledgeUnavailableError as exc:
        logger.warning("Retrieval failed: %s", exc)
        return RetrieveResult(
            chunks=[],
            knowledge_unavailable=True,
            warning="Knowledge store is unavailable; diagnosing without runbooks.",
        )
    except Exception as exc:  # noqa: BLE001 — never fail diagnose because RAG broke
        logger.warning("Retrieval failed unexpectedly: %s", exc)
        return RetrieveResult(
            chunks=[],
            knowledge_unavailable=True,
            warning="Knowledge store is unavailable; diagnosing without runbooks.",
        )


@app.get("/pods/{namespace}/{name}/diagnose", response_model=DiagnoseResponse)
async def diagnose_pod(
    namespace: str,
    name: str,
    stream: bool = Query(default=False, description="If true, return server-sent events"),
    k8s: K8sClient = Depends(get_k8s_client),
    llm: LLMExplainer = Depends(get_llm_explainer),
    retriever: Retriever = Depends(get_retriever),
):
    pod, events, logs = _collect_evidence(k8s, namespace, name)
    retrieved = _retrieve(retriever, pod, events, logs)

    if stream:
        return StreamingResponse(
            _diagnosis_sse(llm, pod, events, logs, retrieved),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        diagnosis = llm.explain(pod, events, logs, retrieved=retrieved.chunks)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DiagnoseResponse(
        pod=pod,
        events=events,
        logs=logs,
        diagnosis=diagnosis,
        citations=_citations(retrieved.chunks),
        knowledge_unavailable=retrieved.knowledge_unavailable,
    )


def _sse(event: str, data: dict | list | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


async def _diagnosis_sse(
    llm: LLMExplainer,
    pod,
    events,
    logs,
    retrieved: RetrieveResult,
) -> AsyncIterator[str]:
    citations = [c.model_dump(mode="json") for c in _citations(retrieved.chunks)]
    yield _sse(
        "context",
        {
            "pod": pod.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "logs": logs.model_dump(mode="json"),
            "citations": citations,
            "knowledge_unavailable": retrieved.knowledge_unavailable,
        },
    )
    yield _sse(
        "citations",
        {
            "citations": citations,
            "knowledge_unavailable": retrieved.knowledge_unavailable,
        },
    )
    if retrieved.knowledge_unavailable:
        yield _sse("warning", {"knowledge_unavailable": True, "detail": retrieved.warning})

    chunks: list[str] = []
    try:
        async for piece in llm.aexplain_stream(pod, events, logs, retrieved=retrieved.chunks):
            chunks.append(piece)
            yield _sse("token", json.dumps({"text": piece}))
    except LLMError as exc:
        yield _sse("error", {"detail": str(exc)})
        return

    text = "".join(chunks)
    try:
        diagnosis: Diagnosis = parse_diagnosis(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        diagnosis = fallback_diagnosis(text, pod)
    yield _sse(
        "diagnosis",
        {
            **diagnosis.model_dump(mode="json"),
            "citations": citations,
            "knowledge_unavailable": retrieved.knowledge_unavailable,
        },
    )
    yield _sse(
        "done",
        {"citations": citations, "knowledge_unavailable": retrieved.knowledge_unavailable},
    )


@app.post("/incidents", response_model=IncidentSaved)
def save_incident(
    body: IncidentCreate,
    retriever: Retriever = Depends(get_retriever),
) -> IncidentSaved:
    try:
        point_id = retriever.save_incident(
            pod_name=body.pod_name,
            namespace=body.namespace,
            reason=body.reason,
            root_cause=body.root_cause,
            fix=body.fix,
            notes=body.notes,
        )
    except KnowledgeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return IncidentSaved(id=point_id, title=f"{body.namespace}/{body.pod_name}")


@app.get("/knowledge/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    q: str = Query(..., min_length=1, description="Free-text search over runbooks and incidents"),
    k: int = Query(default=5, ge=1, le=20),
    retriever: Retriever = Depends(get_retriever),
) -> KnowledgeSearchResponse:
    result = retriever.search(q, top_k=k)
    return KnowledgeSearchResponse(
        query=result.query or q,
        results=[
            KnowledgeHit(
                title=chunk.title,
                source=chunk.source,
                section=chunk.section,
                score=round(float(chunk.score), 4),
                kind=chunk.kind,
                text=chunk.text,
            )
            for chunk in result.chunks
        ],
        knowledge_unavailable=result.knowledge_unavailable,
        warning=result.warning,
    )
