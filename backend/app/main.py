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
from app.deps import get_k8s_client, get_llm_explainer
from app.k8s_client import K8sClient, K8sUnavailableError, PodNotFoundError
from app.llm_explainer import LLMError, LLMExplainer, fallback_diagnosis, parse_diagnosis
from app.models import (
    DiagnoseResponse,
    Diagnosis,
    HealthResponse,
    UnhealthyPodsResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(
        title="Kube Copilot",
        description="AI incident copilot for Kubernetes — Phase 1 diagnoses unhealthy pods with an LLM.",
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


@app.get("/pods/{namespace}/{name}/diagnose", response_model=DiagnoseResponse)
async def diagnose_pod(
    namespace: str,
    name: str,
    stream: bool = Query(default=False, description="If true, return server-sent events"),
    k8s: K8sClient = Depends(get_k8s_client),
    llm: LLMExplainer = Depends(get_llm_explainer),
):
    pod, events, logs = _collect_evidence(k8s, namespace, name)

    if stream:
        return StreamingResponse(
            _diagnosis_sse(llm, pod, events, logs),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        diagnosis = llm.explain(pod, events, logs)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DiagnoseResponse(pod=pod, events=events, logs=logs, diagnosis=diagnosis)


def _sse(event: str, data: dict | list | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


async def _diagnosis_sse(
    llm: LLMExplainer,
    pod,
    events,
    logs,
) -> AsyncIterator[str]:
    yield _sse(
        "context",
        {
            "pod": pod.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "logs": logs.model_dump(mode="json"),
        },
    )
    chunks: list[str] = []
    try:
        async for piece in llm.aexplain_stream(pod, events, logs):
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
    yield _sse("diagnosis", diagnosis.model_dump(mode="json"))
    yield _sse("done", {})
