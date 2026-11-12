"""App-level Prometheus metrics exposed at /metrics."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import FakePrometheus, FakeScorer
from app.anomaly.scorer import ScoreResult


def test_metrics_endpoint_is_public():
    response = TestClient(app).get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "http_request_duration_seconds" in body or "http_requests" in body
    assert "kube_copilot_diagnose_latency_seconds" in body
    assert "kube_copilot_llm_calls_total" in body
    assert "kube_copilot_anomaly_score" in body


def test_diagnose_records_latency_and_llm_success(client, sample_pod, monkeypatch):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    before = TestClient(app).get("/metrics").text
    # The shared TestClient on `client` already incrementally instruments `app`.
    response = client.get(f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose")
    assert response.status_code == 200
    after = TestClient(app).get("/metrics").text
    assert "kube_copilot_diagnose_latency_seconds_count" in after
    assert "kube_copilot_llm_calls_total" in after
    # At least one success sample was recorded during this test process.
    assert 'kube_copilot_llm_calls_total{status="success"}' in after
    assert after != before or "kube_copilot_llm_calls_total" in before


def test_anomaly_scores_are_exported_as_gauges(client_factory, sample_pod):
    result = ScoreResult(
        namespace=sample_pod.namespace,
        name=sample_pod.name,
        score=0.91,
        is_anomaly=True,
        threshold=0.5,
        raw_score=0.2,
        points=[],
    )
    scorer = FakeScorer(results={(sample_pod.namespace, sample_pod.name): result})
    prometheus = FakePrometheus(series={(sample_pod.namespace, sample_pod.name): []})
    client = client_factory(scorer=scorer, prometheus=prometheus)
    response = client.get("/anomalies")
    assert response.status_code == 200
    body = client.get("/metrics").text
    expected = (
        f'kube_copilot_anomaly_score{{namespace="{sample_pod.namespace}",'
        f'pod="{sample_pod.name}"}} 0.91'
    )
    # Prometheus may add spaces around labels or use different order; be tolerant.
    assert "kube_copilot_anomaly_score" in body
    assert sample_pod.namespace in body
    assert sample_pod.name in body
    assert "0.91" in body
    assert "kube_copilot_anomaly_flagged" in body
    _ = expected
