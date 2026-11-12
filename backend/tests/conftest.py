"""Shared fixtures. Kubernetes and LLM clients are mocked so tests need no cluster or API key."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.deps import get_k8s_client, get_llm_explainer
from app.main import app
from app.models import Diagnosis, PodEvent, PodLogs, PodSummary


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

    def explain(self, pod, events, logs):
        from app.llm_explainer import LLMError

        self.calls.append(("explain", pod, events, logs))
        if self.error:
            raise LLMError(self.error)
        return self.diagnosis

    def explain_stream(self, pod, events, logs):
        from app.llm_explainer import LLMError

        self.calls.append(("stream", pod, events, logs))
        if self.error:
            raise LLMError(self.error)
        chunks = self.stream_chunks
        if chunks is None and self.diagnosis is not None:
            chunks = [self.diagnosis.model_dump_json()]
        for piece in chunks or []:
            yield piece

    async def aexplain_stream(self, pod, events, logs):
        for piece in self.explain_stream(pod, events, logs):
            yield piece


@pytest.fixture
def client_factory():
    """Build a TestClient with k8s and LLM dependencies overridden."""

    def _make(k8s=None, llm=None) -> TestClient:
        if k8s is not None:
            app.dependency_overrides[get_k8s_client] = lambda: k8s
        if llm is not None:
            app.dependency_overrides[get_llm_explainer] = lambda: llm
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
