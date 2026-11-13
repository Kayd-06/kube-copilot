"""Lifecycle signals in /anomalies, /disruptions, diagnose and the k8s adapter."""

import time
from datetime import datetime, timedelta, timezone

from kubernetes.client import (
    V1ContainerState,
    V1ContainerStateTerminated,
    V1ContainerStatus,
    V1Deployment,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1LabelSelector,
    V1ObjectMeta,
    V1ObjectReference,
    V1OwnerReference,
    V1Pod,
    V1PodStatus,
    V1PodTemplateSpec,
    V1ReplicaSet,
    V1ReplicaSetSpec,
    CoreV1Event,
)

from app.k8s_client import lifecycle_from_objects
from app.llm_explainer import build_user_prompt
from ml.features import MetricPoint
from ml.lifecycle import DetectorConfig, Signal, detect
from tests.conftest import FakeLifecycle, FakePrometheus, FakeScorer

POD = "victim-kill-6cbc746867-qm64b"


def _points(n=8):
    now = time.time()
    return [
        MetricPoint(ts=now - (n - i) * 15, cpu=0.01, memory_bytes=8e6, memory_limit_bytes=64e6,
                    restarts=0, net_rx_bytes=i * 100.0, net_tx_bytes=i * 50.0)
        for i in range(n)
    ]


def _kill_signal(ago=30.0):
    return Signal(time.time() - ago, "chaos-lab", "victim-kill", "victim-kill-6cbc746867-fxnbp", "event:Killing",
                  "Stopping container web")


def test_anomalies_union_flags_pod_with_lifecycle_signal(client_factory):
    prometheus = FakePrometheus(series={("chaos-lab", POD): _points(), ("chaos-lab", "steady-8496d4f876-b8989"): _points()})
    lifecycle = FakeLifecycle(signals=[_kill_signal()], pod_workloads={("chaos-lab", POD): "victim-kill"})
    body = client_factory(scorer=FakeScorer(), prometheus=prometheus, lifecycle=lifecycle).get("/anomalies").json()
    by_name = {p["name"]: p for p in body["pods"]}
    flagged = by_name[POD]
    assert flagged["is_anomaly"] is True
    assert flagged["metric_anomaly"] is False
    assert flagged["detectors"] == ["lifecycle"]
    assert flagged["lifecycle_signals"][0]["kind"] == "event:Killing"
    assert by_name["steady-8496d4f876-b8989"]["is_anomaly"] is False
    assert body["pods"][0]["name"] == POD  # flagged first
    assert body["lifecycle_unavailable"] is False
    assert body["disruptions"][0]["workload"] == "victim-kill"
    assert body["disruptions"][0]["pods"] == ["victim-kill-6cbc746867-fxnbp"]
    assert body["disruptions"][0]["summary"] == "Killing x1"


def test_anomalies_flagged_only_keeps_lifecycle_only_pods(client_factory):
    prometheus = FakePrometheus(series={("chaos-lab", POD): _points()})
    lifecycle = FakeLifecycle(signals=[_kill_signal()], pod_workloads={("chaos-lab", POD): "victim-kill"})
    body = client_factory(scorer=FakeScorer(), prometheus=prometheus, lifecycle=lifecycle).get(
        "/anomalies?flagged_only=true"
    ).json()
    assert [p["name"] for p in body["pods"]] == [POD]


def test_old_signals_fall_outside_the_lookback(client_factory):
    prometheus = FakePrometheus(series={("chaos-lab", POD): _points()})
    lifecycle = FakeLifecycle(signals=[_kill_signal(ago=600)], pod_workloads={("chaos-lab", POD): "victim-kill"})
    body = client_factory(scorer=FakeScorer(), prometheus=prometheus, lifecycle=lifecycle).get("/anomalies").json()
    assert body["pods"][0]["is_anomaly"] is False


def test_anomalies_still_work_without_kubernetes(client_factory):
    prometheus = FakePrometheus(series={("chaos-lab", POD): _points()})
    body = client_factory(
        scorer=FakeScorer(), prometheus=prometheus, lifecycle=FakeLifecycle(unavailable=True)
    ).get("/anomalies").json()
    assert body["count"] == 1
    assert body["lifecycle_unavailable"] is True
    assert body["disruptions"] == []


def test_disruptions_endpoint(client_factory):
    lifecycle = FakeLifecycle(signals=[_kill_signal(), _kill_signal(ago=10)])
    response = client_factory(lifecycle=lifecycle).get("/disruptions?namespace=chaos-lab")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["disruptions"][0]["summary"] == "Killing x2"
    assert lifecycle.calls == ["chaos-lab"]
    assert client_factory(lifecycle=FakeLifecycle(unavailable=True)).get("/disruptions").status_code == 503


def test_prompt_lists_lifecycle_signals(sample_pod, sample_events, sample_logs):
    from app.main import to_anomaly_score
    from app.anomaly.scorer import ScoreResult

    result = ScoreResult("chaos-lab", POD, 0.2, False, 0.5, -0.1, points=_points())
    anomaly = to_anomaly_score(result, [_kill_signal()])
    prompt = build_user_prompt(sample_pod, sample_events, sample_logs, [], anomaly)
    assert "flagged_as_anomaly: false" in prompt
    assert "Lifecycle detector" in prompt
    assert "event:Killing pod=victim-kill-6cbc746867-fxnbp Stopping container web" in prompt


def test_lifecycle_from_objects_builds_detector_inputs():
    now = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    rs_created = now - timedelta(days=1)
    pod = V1Pod(
        metadata=V1ObjectMeta(
            name="web-abc1234567-ccccc", namespace="apps", creation_timestamp=now - timedelta(seconds=40),
            owner_references=[V1OwnerReference(api_version="apps/v1", kind="ReplicaSet", name="web-abc1234567", uid="r")],
        ),
        status=V1PodStatus(container_statuses=[
            V1ContainerStatus(
                name="web", image="nginx", image_id="", ready=True, restart_count=1,
                last_state=V1ContainerState(terminated=V1ContainerStateTerminated(
                    exit_code=137, reason="OOMKilled", finished_at=now - timedelta(seconds=20))),
            )
        ]),
    )
    rs = V1ReplicaSet(
        metadata=V1ObjectMeta(name="web-abc1234567", namespace="apps", creation_timestamp=rs_created),
        spec=V1ReplicaSetSpec(selector=V1LabelSelector()),
    )
    dep = V1Deployment(
        metadata=V1ObjectMeta(name="web", namespace="apps", generation=3),
        spec=V1DeploymentSpec(replicas=2, selector=V1LabelSelector(), template=V1PodTemplateSpec()),
        status=V1DeploymentStatus(available_replicas=1),
    )
    events = [
        CoreV1Event(
            metadata=V1ObjectMeta(name="e1", namespace="apps"),
            involved_object=V1ObjectReference(kind="Pod", name="web-abc1234567-aaaaa", namespace="apps"),
            reason="Killing", type="Normal", message="Stopping container web",
            last_timestamp=now - timedelta(seconds=41), first_timestamp=now - timedelta(seconds=41),
        ),
        CoreV1Event(
            metadata=V1ObjectMeta(name="e2", namespace="apps"),
            involved_object=V1ObjectReference(kind="PodChaos", name="pod-kill", namespace="apps"),
            reason="Applied", last_timestamp=now - timedelta(seconds=41),
        ),
        CoreV1Event(
            metadata=V1ObjectMeta(name="e3", namespace="apps"),
            involved_object=V1ObjectReference(kind="Pod", name="old", namespace="apps"),
            reason="Killing", last_timestamp=now - timedelta(hours=1),
        ),
    ]
    ts = now.timestamp()
    ev, pods, samples, restarts = lifecycle_from_objects(events, [pod], [dep], [rs], ts, 180)
    assert [(e.kind, e.name) for e in ev] == [("Pod", "web-abc1234567-aaaaa")]
    assert pods[0].workload == "web" and pods[0].owner_created_ts == rs_created.timestamp()
    assert (samples[0].desired, samples[0].available) == (2.0, 1.0)
    assert restarts[0].reason == "OOMKilled"
    kinds = sorted(s.kind for s in detect(ev, pods, samples, restarts, DetectorConfig(observation_start=ts - 180)))
    assert kinds == ["container_restarted", "event:Killing", "pod_replaced", "replicas_unavailable"]
