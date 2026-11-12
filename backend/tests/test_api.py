import json

from app.k8s_client import to_pod_summary
from app.main import app
from tests.conftest import FakeK8s, FakeLLM


def test_list_unhealthy_pods(client, sample_pod):
    response = client.get("/pods/unhealthy")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["pods"][0]["name"] == sample_pod.name
    assert body["pods"][0]["reason"] == "CrashLoopBackOff"


def test_list_unhealthy_pods_namespace_filter(client_factory, sample_pod):
    other = sample_pod.model_copy(update={"name": "other", "namespace": "kube-system"})
    k8s = FakeK8s(pods=[sample_pod, other], evidence={})
    response = client_factory(k8s=k8s, llm=FakeLLM()).get("/pods/unhealthy", params={"namespace": "copilot-demo"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["pods"][0]["namespace"] == "copilot-demo"


def test_unhealthy_pods_returns_503_when_cluster_down(client_factory):
    k8s = FakeK8s(unavailable=True)
    response = client_factory(k8s=k8s, llm=FakeLLM()).get("/pods/unhealthy")
    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]


def test_diagnose_success(client, sample_pod, sample_diagnosis, monkeypatch):
    # _collect_evidence calls k8s.get_pod then to_pod_summary. FakeK8s returns the
    # PodSummary itself; patch to_pod_summary to pass it through.
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod if hasattr(pod, "reason") else to_pod_summary(pod))
    # The import inside _collect_evidence is `from app.k8s_client import to_pod_summary`
    # so we also patch the name used in main after import... actually it's imported
    # inside the function, so patching app.k8s_client.to_pod_summary is correct.
    response = client.get(f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose")
    assert response.status_code == 200
    body = response.json()
    assert body["pod"]["name"] == sample_pod.name
    assert body["diagnosis"]["likely_root_cause"] == sample_diagnosis.likely_root_cause
    assert body["diagnosis"]["confidence"] == sample_diagnosis.confidence
    assert body["logs"]["previous"] is not None
    assert len(body["events"]) == 2


def test_diagnose_404(client_factory, sample_diagnosis, monkeypatch):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    k8s = FakeK8s(pods=[], evidence={}, missing={("copilot-demo", "nope")})
    response = client_factory(k8s=k8s, llm=FakeLLM(diagnosis=sample_diagnosis)).get(
        "/pods/copilot-demo/nope/diagnose"
    )
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_diagnose_llm_error_is_502(client_factory, sample_pod, sample_events, sample_logs, monkeypatch):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={(sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)},
    )
    llm = FakeLLM(error="quota exceeded")
    response = client_factory(k8s=k8s, llm=llm).get(f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose")
    assert response.status_code == 502
    assert "quota exceeded" in response.json()["detail"]


def test_diagnose_stream(client, sample_pod, sample_diagnosis, monkeypatch):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    response = client.get(
        f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose",
        params={"stream": "true"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert "event: context" in text
    assert "event: token" in text
    assert "event: diagnosis" in text
    assert "event: done" in text
    # The streamed diagnosis should match the fake model output (JSON of Diagnosis).
    assert sample_diagnosis.likely_root_cause in text
