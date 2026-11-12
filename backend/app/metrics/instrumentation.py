"""Prometheus metrics exposed at GET /metrics.

HTTP request metrics come from prometheus-fastapi-instrumentator. The
histogram, counter, and gauges below are application-specific so Grafana
can chart diagnose latency, LLM traffic, and per-pod anomaly scores.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

DIAGNOSE_LATENCY = Histogram(
    "kube_copilot_diagnose_latency_seconds",
    "End-to-end latency of a pod diagnosis (evidence, retrieval, scoring, LLM).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

LLM_CALLS = Counter(
    "kube_copilot_llm_calls_total",
    "LLM explainer invocations.",
    ["status"],  # success | error
)

ANOMALY_SCORE = Gauge(
    "kube_copilot_anomaly_score",
    "Latest anomaly score for a pod (0-1; 0.5 is the decision boundary).",
    ["namespace", "pod"],
)

ANOMALY_FLAGGED = Gauge(
    "kube_copilot_anomaly_flagged",
    "1 if the pod was last scored as anomalous, otherwise 0.",
    ["namespace", "pod"],
)


def instrument_app(application) -> None:
    """Attach default HTTP metrics and expose them at /metrics."""
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers={"/metrics"},
    ).instrument(application).expose(
        application,
        endpoint="/metrics",
        include_in_schema=True,
        should_gzip=False,
    )


@contextmanager
def track_diagnose_latency() -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        DIAGNOSE_LATENCY.observe(time.perf_counter() - start)


def record_llm_call(success: bool) -> None:
    LLM_CALLS.labels(status="success" if success else "error").inc()


def record_anomaly(namespace: str, name: str, score: float, is_anomaly: bool) -> None:
    ANOMALY_SCORE.labels(namespace=namespace, pod=name).set(score)
    ANOMALY_FLAGGED.labels(namespace=namespace, pod=name).set(1.0 if is_anomaly else 0.0)
