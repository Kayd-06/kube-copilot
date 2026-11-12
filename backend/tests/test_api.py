import json

from app.k8s_client import to_pod_summary
from app.main import app
from tests.conftest import FakeK8s, FakeLLM, FakeRetriever


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
    assert body["citations"] == []
    assert body["knowledge_unavailable"] is False


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



def test_diagnose_includes_citations(client_factory, sample_pod, sample_events, sample_logs, sample_diagnosis, sample_chunk, monkeypatch):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={(sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)},
    )
    llm = FakeLLM(diagnosis=sample_diagnosis)
    retriever = FakeRetriever(chunks=[sample_chunk])
    response = client_factory(k8s=k8s, llm=llm, retriever=retriever).get(
        f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["knowledge_unavailable"] is False
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["title"] == "CrashLoopBackOff"
    assert citation["source"] == "crashloopbackoff.md"
    assert citation["section"] == "Common causes"
    assert citation["score"] == 0.91
    # The LLM should have been given the retrieved chunks.
    assert llm.calls
    assert llm.calls[0][0] == "explain"
    assert llm.calls[0][4] == [sample_chunk]


def test_diagnose_falls_back_when_knowledge_unavailable(
    client_factory, sample_pod, sample_events, sample_logs, sample_diagnosis, monkeypatch
):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={(sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)},
    )
    llm = FakeLLM(diagnosis=sample_diagnosis)
    retriever = FakeRetriever(unavailable=True)
    response = client_factory(k8s=k8s, llm=llm, retriever=retriever).get(
        f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis"]["likely_root_cause"] == sample_diagnosis.likely_root_cause
    assert body["citations"] == []
    assert body["knowledge_unavailable"] is True


def test_diagnose_stream_includes_citations(
    client_factory, sample_pod, sample_events, sample_logs, sample_diagnosis, sample_chunk, monkeypatch
):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={(sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)},
    )
    response = client_factory(
        k8s=k8s,
        llm=FakeLLM(diagnosis=sample_diagnosis),
        retriever=FakeRetriever(chunks=[sample_chunk]),
    ).get(
        f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose",
        params={"stream": "true"},
    )
    assert response.status_code == 200
    text = response.text
    assert "event: citations" in text
    assert "crashloopbackoff.md" in text
    assert "event: diagnosis" in text
    assert "event: done" in text


def test_save_incident(client_factory, sample_chunk):
    retriever = FakeRetriever()
    response = client_factory(retriever=retriever).post(
        "/incidents",
        json={
            "pod_name": "crashloop-app-abc123",
            "namespace": "copilot-demo",
            "reason": "CrashLoopBackOff",
            "root_cause": "exit 1 in the command",
            "fix": "Removed the exit 1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "incident-test-id"
    assert body["title"] == "copilot-demo/crashloop-app-abc123"
    assert retriever.saved
    assert retriever.saved[0]["root_cause"] == "exit 1 in the command"


def test_save_incident_returns_503_when_store_is_down(client_factory):
    response = client_factory(retriever=FakeRetriever(unavailable=True)).post(
        "/incidents",
        json={
            "pod_name": "x",
            "namespace": "ns",
            "root_cause": "r",
            "fix": "f",
        },
    )
    assert response.status_code == 503


def test_knowledge_search(client_factory, sample_chunk):
    retriever = FakeRetriever(chunks=[sample_chunk])
    response = client_factory(retriever=retriever).get("/knowledge/search", params={"q": "crash loop"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "crash loop"
    assert body["knowledge_unavailable"] is False
    assert body["results"][0]["source"] == "crashloopbackoff.md"
    assert "exit 1" in body["results"][0]["text"]


def test_knowledge_search_unavailable(client_factory):
    response = client_factory(retriever=FakeRetriever(unavailable=True)).get(
        "/knowledge/search", params={"q": "oom"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["knowledge_unavailable"] is True
