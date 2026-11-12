from app.anomaly.scorer import ScoreResult
from ml.features import MetricPoint
from tests.conftest import FakeLLM, FakePrometheus, FakeScorer


def _points(n=8, mem0=40 * 1024 * 1024, leak=False):
    points = []
    mem = mem0
    for i in range(n):
        if leak:
            mem += 5 * 1024 * 1024
        points.append(
            MetricPoint(
                ts=1_700_000_000 + i * 15,
                cpu=0.08,
                memory_bytes=mem,
                memory_limit_bytes=128 * 1024 * 1024,
                restarts=0,
                net_rx_bytes=i * 1000,
                net_tx_bytes=i * 500,
            )
        )
    return points


def _flagged(ns, name, points, score=0.91):
    return ScoreResult(
        namespace=ns,
        name=name,
        score=score,
        is_anomaly=True,
        threshold=0.5,
        raw_score=0.2,
        contributing_features=[
            {"name": "mem_slope_mib_per_min", "value": 12.0, "zscore": 6.1, "direction": "high"}
        ],
        points=points,
    )


def test_anomalies_lists_scored_pods(client_factory, sample_diagnosis):
    points = _points(leak=True)
    prometheus = FakePrometheus(series={("copilot-demo", "leak-app"): points})
    scorer = FakeScorer(results={("copilot-demo", "leak-app"): _flagged("copilot-demo", "leak-app", points)})
    response = client_factory(scorer=scorer, prometheus=prometheus).get("/anomalies")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["model_loaded"] is True
    pod = body["pods"][0]
    assert pod["name"] == "leak-app"
    assert pod["is_anomaly"] is True
    assert pod["score"] == 0.91
    assert pod["contributing_features"][0]["name"] == "mem_slope_mib_per_min"
    assert len(pod["series"]) == len(points)


def test_anomalies_flagged_only(client_factory):
    healthy = _points()
    leaky = _points(leak=True)
    prometheus = FakePrometheus(
        series={
            ("copilot-demo", "ok"): healthy,
            ("copilot-demo", "leak-app"): leaky,
        }
    )
    scorer = FakeScorer(
        results={
            ("copilot-demo", "leak-app"): _flagged("copilot-demo", "leak-app", leaky),
        }
    )
    response = client_factory(scorer=scorer, prometheus=prometheus).get(
        "/anomalies", params={"flagged_only": "true"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["pods"][0]["name"] == "leak-app"


def test_anomalies_503_when_model_missing(client_factory):
    response = client_factory(scorer=FakeScorer(loaded=False, message="Anomaly model not found. Run make train.")).get(
        "/anomalies"
    )
    assert response.status_code == 503
    assert "make train" in response.json()["detail"]


def test_anomalies_503_when_prometheus_down(client_factory):
    response = client_factory(
        scorer=FakeScorer(),
        prometheus=FakePrometheus(unavailable=True),
    ).get("/anomalies")
    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]


def test_pod_anomaly_success(client_factory):
    points = _points(leak=True)
    prometheus = FakePrometheus(series={("copilot-demo", "leak-app"): points})
    scorer = FakeScorer(results={("copilot-demo", "leak-app"): _flagged("copilot-demo", "leak-app", points)})
    response = client_factory(scorer=scorer, prometheus=prometheus).get(
        "/pods/copilot-demo/leak-app/anomaly"
    )
    assert response.status_code == 200
    assert response.json()["is_anomaly"] is True


def test_pod_anomaly_404_when_no_series(client_factory):
    response = client_factory(scorer=FakeScorer(), prometheus=FakePrometheus(series={})).get(
        "/pods/copilot-demo/missing/anomaly"
    )
    assert response.status_code == 404


def test_diagnose_includes_anomaly(client_factory, sample_pod, sample_events, sample_logs, sample_diagnosis, monkeypatch):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    from tests.conftest import FakeK8s

    points = _points(leak=True)
    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={(sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)},
    )
    llm = FakeLLM(diagnosis=sample_diagnosis)
    scorer = FakeScorer(
        results={(sample_pod.namespace, sample_pod.name): _flagged(sample_pod.namespace, sample_pod.name, points)}
    )
    prometheus = FakePrometheus(series={(sample_pod.namespace, sample_pod.name): points})
    response = client_factory(k8s=k8s, llm=llm, scorer=scorer, prometheus=prometheus).get(
        f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["anomaly_unavailable"] is False
    assert body["anomaly"]["is_anomaly"] is True
    assert body["anomaly"]["contributing_features"][0]["name"] == "mem_slope_mib_per_min"
    # The LLM received the anomaly object.
    assert llm.calls[0][0] == "explain"
    assert llm.calls[0][5] is not None
    assert llm.calls[0][5].is_anomaly is True


def test_diagnose_still_works_when_anomaly_unavailable(
    client_factory, sample_pod, sample_events, sample_logs, sample_diagnosis, monkeypatch
):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    from tests.conftest import FakeK8s

    k8s = FakeK8s(
        pods=[sample_pod],
        evidence={(sample_pod.namespace, sample_pod.name): (sample_pod, sample_events, sample_logs)},
    )
    response = client_factory(
        k8s=k8s,
        llm=FakeLLM(diagnosis=sample_diagnosis),
        scorer=FakeScorer(loaded=False),
        prometheus=FakePrometheus(unavailable=True),
    ).get(f"/pods/{sample_pod.namespace}/{sample_pod.name}/diagnose")
    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis"]["likely_root_cause"] == sample_diagnosis.likely_root_cause
    assert body["anomaly"] is None
    assert body["anomaly_unavailable"] is True
