"""FastAPI entrypoint for Kube Copilot."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import __version__
from app.anomaly.poller import AnomalyPoller
from app.anomaly.scorer import AnomalyModelMissingError, AnomalyScorer, ScoreResult
from app.config import Settings, get_settings
from app.deps import (
    get_anomaly_scorer,
    get_k8s_client,
    get_llm_explainer,
    get_prometheus_client,
    get_retriever,
)
from app.k8s_client import K8sClient, K8sUnavailableError, PodNotFoundError
from app.llm_explainer import LLMError, LLMExplainer, fallback_diagnosis, parse_diagnosis
from app.metrics.prometheus import PrometheusClient, PrometheusUnavailableError
from app.models import (
    AnomaliesResponse,
    AnomalyScore,
    Citation,
    DiagnoseResponse,
    Diagnosis,
    FeatureContribution,
    HealthResponse,
    IncidentCreate,
    IncidentSaved,
    KnowledgeHit,
    KnowledgeSearchResponse,
    MetricSample,
    UnhealthyPodsResponse,
)
from app.rag.retriever import RetrievedChunk, Retriever, RetrieveResult
from app.rag.store import KnowledgeUnavailableError
from ml.features import MIB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_poller: AnomalyPoller | None = None


def get_poller() -> AnomalyPoller | None:
    return _poller


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _poller
    settings = get_settings()
    poller: AnomalyPoller | None = None
    if settings.anomaly_poller_enabled:
        poller = AnomalyPoller(get_anomaly_scorer(), get_prometheus_client(), settings)
        poller.start()
        _poller = poller
    try:
        yield
    finally:
        if poller is not None:
            poller.stop()
        _poller = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(
        title="Kube Copilot",
        description=(
            "AI incident copilot for Kubernetes — Phase 3 diagnoses unhealthy "
            "pods with an LLM, citing retrieved runbooks, and flags metric "
            "anomalies with an Isolation Forest trained on synthetic data."
        ),
        version=__version__,
        lifespan=lifespan,
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


def _series_from_result(result: ScoreResult) -> list[MetricSample]:
    samples: list[MetricSample] = []
    for point in result.points:
        limit = point.memory_limit_bytes
        ratio = (point.memory_bytes / limit) if limit else 0.0
        samples.append(
            MetricSample(
                ts=point.ts,
                cpu=round(point.cpu, 4),
                memory_mib=round(point.memory_bytes / MIB, 3),
                mem_to_limit_ratio=round(min(max(ratio, 0.0), 2.0), 4),
                restarts=point.restarts,
            )
        )
    return samples


def to_anomaly_score(result: ScoreResult) -> AnomalyScore:
    return AnomalyScore(
        namespace=result.namespace,
        name=result.name,
        score=round(result.score, 4),
        is_anomaly=result.is_anomaly,
        threshold=round(result.threshold, 6),
        contributing_features=[FeatureContribution(**item) for item in result.contributing_features],
        series=_series_from_result(result),
        warning=result.warning,
    )


def _score_pod(
    scorer: AnomalyScorer,
    prometheus: PrometheusClient,
    namespace: str,
    name: str,
) -> tuple[AnomalyScore | None, bool]:
    """Best-effort scoring used by diagnose. Never raises."""
    try:
        if not scorer.loaded:
            return None, True
        points = prometheus.pod_metric_series(namespace, name)
        return to_anomaly_score(scorer.score_window(namespace, name, points)), False
    except (AnomalyModelMissingError, PrometheusUnavailableError) as exc:
        logger.info("Anomaly scoring skipped for %s/%s: %s", namespace, name, exc)
        return None, True
    except Exception as exc:  # noqa: BLE001 — diagnose must still succeed
        logger.warning("Anomaly scoring failed for %s/%s: %s", namespace, name, exc)
        return None, True


@app.get("/pods/{namespace}/{name}/diagnose", response_model=DiagnoseResponse)
async def diagnose_pod(
    namespace: str,
    name: str,
    stream: bool = Query(default=False, description="If true, return server-sent events"),
    k8s: K8sClient = Depends(get_k8s_client),
    llm: LLMExplainer = Depends(get_llm_explainer),
    retriever: Retriever = Depends(get_retriever),
    scorer: AnomalyScorer = Depends(get_anomaly_scorer),
    prometheus: PrometheusClient = Depends(get_prometheus_client),
):
    pod, events, logs = _collect_evidence(k8s, namespace, name)
    retrieved = _retrieve(retriever, pod, events, logs)
    anomaly, anomaly_unavailable = _score_pod(scorer, prometheus, namespace, name)

    if stream:
        return StreamingResponse(
            _diagnosis_sse(llm, pod, events, logs, retrieved, anomaly, anomaly_unavailable),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        diagnosis = llm.explain(pod, events, logs, retrieved=retrieved.chunks, anomaly=anomaly)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DiagnoseResponse(
        pod=pod,
        events=events,
        logs=logs,
        diagnosis=diagnosis,
        citations=_citations(retrieved.chunks),
        knowledge_unavailable=retrieved.knowledge_unavailable,
        anomaly=anomaly,
        anomaly_unavailable=anomaly_unavailable,
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
    anomaly: AnomalyScore | None = None,
    anomaly_unavailable: bool = False,
) -> AsyncIterator[str]:
    citations = [c.model_dump(mode="json") for c in _citations(retrieved.chunks)]
    anomaly_payload = anomaly.model_dump(mode="json") if anomaly is not None else None
    yield _sse(
        "context",
        {
            "pod": pod.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "logs": logs.model_dump(mode="json"),
            "citations": citations,
            "knowledge_unavailable": retrieved.knowledge_unavailable,
            "anomaly": anomaly_payload,
            "anomaly_unavailable": anomaly_unavailable,
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
    if anomaly_unavailable:
        yield _sse(
            "warning",
            {
                "anomaly_unavailable": True,
                "detail": "Anomaly score was unavailable for this diagnosis.",
            },
        )

    chunks: list[str] = []
    try:
        async for piece in llm.aexplain_stream(
            pod, events, logs, retrieved=retrieved.chunks, anomaly=anomaly
        ):
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
            "anomaly": anomaly_payload,
            "anomaly_unavailable": anomaly_unavailable,
        },
    )
    yield _sse(
        "done",
        {
            "citations": citations,
            "knowledge_unavailable": retrieved.knowledge_unavailable,
            "anomaly": anomaly_payload,
            "anomaly_unavailable": anomaly_unavailable,
        },
    )


@app.get("/anomalies", response_model=AnomaliesResponse)
def list_anomalies(
    namespace: str | None = Query(default=None, description="Limit scoring to one namespace"),
    flagged_only: bool = Query(default=False, description="Only return pods flagged as anomalous"),
    scorer: AnomalyScorer = Depends(get_anomaly_scorer),
    prometheus: PrometheusClient = Depends(get_prometheus_client),
) -> AnomaliesResponse:
    if not scorer.loaded:
        raise HTTPException(status_code=503, detail=scorer.missing_message)
    try:
        series = prometheus.list_pod_series(namespace=namespace)
    except PrometheusUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    scores: list[AnomalyScore] = []
    for (ns, name), points in sorted(series.items()):
        result = scorer.score_window(ns, name, points)
        if flagged_only and not result.is_anomaly:
            continue
        scores.append(to_anomaly_score(result))
    scores.sort(key=lambda item: item.score, reverse=True)
    return AnomaliesResponse(pods=scores, count=len(scores), model_loaded=True)


@app.get("/pods/{namespace}/{name}/anomaly", response_model=AnomalyScore)
def get_pod_anomaly(
    namespace: str,
    name: str,
    scorer: AnomalyScorer = Depends(get_anomaly_scorer),
    prometheus: PrometheusClient = Depends(get_prometheus_client),
) -> AnomalyScore:
    if not scorer.loaded:
        raise HTTPException(status_code=503, detail=scorer.missing_message)
    try:
        points = prometheus.pod_metric_series(namespace, name)
    except PrometheusUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not points:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No Prometheus series found for {namespace}/{name}. "
                "Is metrics-up running, and has the pod been scraping for a minute?"
            ),
        )
    return to_anomaly_score(scorer.score_window(namespace, name, points))


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
