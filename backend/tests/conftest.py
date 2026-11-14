"""Shared fixtures. Kubernetes and LLM clients are mocked so tests need no cluster or API key."""

from __future__ import annotations

import os

# Must run before app imports: Settings and embedder factories read these.
os.environ.setdefault("EMBEDDING_BACKEND", "fake")
os.environ.setdefault("QDRANT_URL", ":memory:")

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.anomaly.lifecycle import LifecycleReport
from app.deps import (
    get_anomaly_scorer,
    get_k8s_client,
    get_lifecycle_detector,
    get_llm_explainer,
    get_prometheus_client,
    get_retriever,
)
from app.main import app
from app.models import Diagnosis, PodEvent, PodLogs, PodSummary
from app.rag.retriever import RetrievedChunk, RetrieveResult
from app.anomaly.scorer import AnomalyModelMissingError, ScoreResult
from app.metrics.prometheus import PrometheusUnavailableError
from app.rag.store import KnowledgeUnavailableError


@pytest.fixture
def sample_pod() -> PodSummary:
    return PodSummary(
        name="crashloop-app-abc123",
        namespace="copilot-demo",
        phase="Running",
        reason="CrashLoopBackOff",
        message="back-off 5m0s restarting failed container=crash",
        restart_count=8,
        ready=False,
        created_at=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc),
        node="kube-copilot-control-plane",
        qos_class="BestEffort",
        containers=[],
    )


@pytest.fixture
def sample_events() -> list[PodEvent]:
    return [
        PodEvent(
            type="Warning",
            reason="BackOff",
            message="Back-off restarting failed container crash in pod crashloop-app-abc123",
            count=12,
            last_timestamp=datetime(2026, 11, 12, 10, 5, tzinfo=timezone.utc),
        ),
        PodEvent(
            type="Warning",
            reason="Failed",
            message="Error: failed to start container",
            count=3,
            last_timestamp=datetime(2026, 11, 12, 10, 1, tzinfo=timezone.utc),
        ),
    ]


@pytest.fixture
def sample_logs() -> PodLogs:
    return PodLogs(
        current="(container is waiting: CrashLoopBackOff)",
        previous="2026-11-12T10:00:00Z I am about to crash\n",
        container="crash",
    )


@pytest.fixture
def sample_diagnosis() -> Diagnosis:
    return Diagnosis(
        summary="The crash container exits with code 1 shortly after starting, and Kubernetes keeps restarting it.",
        likely_root_cause="The container command ends with `exit 1`, causing a CrashLoopBackOff.",
        suggested_fix="Fix the process so it stays running, or change the command / args in the pod spec.",
        kubectl_commands=[
            "kubectl describe pod crashloop-app-abc123 -n copilot-demo",
            "kubectl logs crashloop-app-abc123 -n copilot-demo --previous --tail=200",
        ],
        confidence=0.86,
    )


class FakeK8s:
    def __init__(self, pods=None, evidence=None, missing=None, unavailable=False):
        self.pods = pods or []
        # evidence: (namespace, name) -> (pod, events, logs)
        self.evidence = evidence or {}
        self.missing = set(missing or [])
        self.unavailable = unavailable
        self.raw_pods = {}

    def list_unhealthy_pods(self, namespace=None):
        from app.k8s_client import K8sUnavailableError

        if self.unavailable:
            raise K8sUnavailableError("cluster unreachable")
        if namespace:
            return [p for p in self.pods if p.namespace == namespace]
        return list(self.pods)

    def get_pod(self, namespace, name):
        from app.k8s_client import K8sUnavailableError, PodNotFoundError

        if self.unavailable:
            raise K8sUnavailableError("cluster unreachable")
        key = (namespace, name)
        if key in self.missing or key not in self.evidence:
            raise PodNotFoundError(namespace, name)
        # Return a sentinel; to_pod_summary is patched in API tests when needed.
        return self.raw_pods.get(key, self.evidence[key][0])

    def get_pod_events(self, namespace, name):
        return self.evidence[(namespace, name)][1]

    def get_pod_logs(self, namespace, name, pod=None):
        return self.evidence[(namespace, name)][2]


class FakeLLM:
    def __init__(self, diagnosis=None, error=None, stream_chunks=None):
        self.diagnosis = diagnosis
        self.error = error
        self.stream_chunks = stream_chunks
        self.calls = []

    def explain(self, pod, events, logs, retrieved=None, anomaly=None, usage=None):
        from app.llm_explainer import LLMError

        self.calls.append(("explain", pod, events, logs, retrieved, anomaly))
        if self.error:
            raise LLMError(self.error)
        return self.diagnosis

    def explain_stream(self, pod, events, logs, retrieved=None, anomaly=None, usage=None):
        from app.llm_explainer import LLMError

        self.calls.append(("stream", pod, events, logs, retrieved, anomaly))
        if self.error:
            raise LLMError(self.error)
        chunks = self.stream_chunks
        if chunks is None and self.diagnosis is not None:
            chunks = [self.diagnosis.model_dump_json()]
        for piece in chunks or []:
            yield piece

    async def aexplain_stream(self, pod, events, logs, retrieved=None, anomaly=None, usage=None):
        for piece in self.explain_stream(pod, events, logs, retrieved, anomaly):
            yield piece




class FakeRetriever:
    def __init__(self, chunks=None, unavailable=False, warning=None):
        self.chunks = list(chunks or [])
        self.unavailable = unavailable
        self.warning = warning or (
            "Knowledge store is unavailable; diagnosing without runbooks."
            if unavailable
            else None
        )
        self.saved = []
        self.queries = []

    def retrieve_for_pod(self, pod, events, logs, top_k=None, usage=None):
        self.queries.append(("pod", pod, events, logs, top_k))
        return RetrieveResult(
            chunks=list(self.chunks),
            knowledge_unavailable=self.unavailable,
            warning=self.warning,
            query="crashloop",
        )

    def search(self, query, top_k=None):
        self.queries.append(("search", query, top_k))
        return RetrieveResult(
            chunks=list(self.chunks),
            query=query,
            knowledge_unavailable=self.unavailable,
            warning=self.warning,
        )

    def save_incident(self, **kwargs):
        if self.unavailable:
            raise KnowledgeUnavailableError(
                "Knowledge store is unavailable; cannot save the incident."
            )
        self.saved.append(kwargs)
        return "incident-test-id"



@pytest.fixture
def sample_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        title="CrashLoopBackOff",
        source="crashloopbackoff.md",
        section="Common causes",
        text="The container command or entrypoint exits immediately (`exit 1`).",
        score=0.91,
        kind="runbook",
    )



class FakePrometheus:
    def __init__(self, series=None, unavailable=False):
        self.series = dict(series or {})
        self.unavailable = unavailable

    def is_available(self):
        return not self.unavailable

    def pod_metric_series(self, namespace, name, window_seconds=None, step_seconds=None):
        if self.unavailable:
            raise PrometheusUnavailableError("prometheus unreachable")
        return list(self.series.get((namespace, name), []))

    def list_pod_series(self, namespace=None, window_seconds=None, step_seconds=None):
        if self.unavailable:
            raise PrometheusUnavailableError("prometheus unreachable")
        items = dict(self.series)
        if namespace:
            items = {key: value for key, value in items.items() if key[0] == namespace}
        return items


class FakeScorer:
    def __init__(self, results=None, loaded=True, message="Anomaly model not found."):
        self.results = dict(results or {})
        self.loaded = loaded
        self.missing_message = message
        self.calls = []

    def require_loaded(self):
        if not self.loaded:
            raise AnomalyModelMissingError(self.missing_message)
        return {}

    def score_window(self, namespace, name, points):
        self.calls.append((namespace, name, points))
        if not self.loaded:
            raise AnomalyModelMissingError(self.missing_message)
        if (namespace, name) in self.results:
            return self.results[(namespace, name)]
        return ScoreResult(
            namespace=namespace,
            name=name,
            score=0.2,
            is_anomaly=False,
            threshold=0.5,
            raw_score=-0.1,
            points=points,
        )


class FakeLifecycle:
    """Stands in for LifecycleDetector so tests never read a real cluster."""

    def __init__(self, signals=None, pod_workloads=None, unavailable=False, lookback_s=180.0):
        self.signals = list(signals or [])
        self.pod_workloads = dict(pod_workloads or {})
        self.unavailable = unavailable
        self.lookback_s = lookback_s
        self.calls = []

    def snapshot(self, namespace=None, now=None):
        from app.k8s_client import K8sUnavailableError

        self.calls.append(namespace)
        if self.unavailable:
            raise K8sUnavailableError("cluster unreachable")
        signals = [s for s in self.signals if namespace is None or s.namespace == namespace]
        return LifecycleReport(signals=signals, pod_workloads=self.pod_workloads, lookback_s=self.lookback_s)


@pytest.fixture
def client_factory():
    """Build a TestClient with k8s and LLM dependencies overridden."""

    def _make(k8s=None, llm=None, retriever=None, scorer=None, prometheus=None, lifecycle=None) -> TestClient:
        if k8s is not None:
            app.dependency_overrides[get_k8s_client] = lambda: k8s
        if llm is not None:
            app.dependency_overrides[get_llm_explainer] = lambda: llm
        if retriever is not None:
            app.dependency_overrides[get_retriever] = lambda: retriever
        else:
            # Default: available store with no hits, so existing tests stay deterministic.
            app.dependency_overrides[get_retriever] = lambda: FakeRetriever()
        # Default: no model and no Prometheus, so diagnose tests do not hit the network.
        app.dependency_overrides[get_anomaly_scorer] = lambda: scorer if scorer is not None else FakeScorer(loaded=False)
        app.dependency_overrides[get_prometheus_client] = (
            lambda: prometheus if prometheus is not None else FakePrometheus()
        )
        app.dependency_overrides[get_lifecycle_detector] = (
            lambda: lifecycle if lifecycle is not None else FakeLifecycle()
        )
        test_client = TestClient(app)
        return test_client

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_factory, sample_pod, sample_events, sample_logs, sample_diagnosis):
    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={
            (sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)
        },
    )
    llm = FakeLLM(diagnosis=sample_diagnosis)
    return client_factory(k8s=k8s, llm=llm)
