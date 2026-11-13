"""Rules in ml/lifecycle.py: pod kills, replacements, replica dips, restarts."""

from ml.lifecycle import (
    DetectorConfig,
    LifecycleEvent,
    PodLife,
    RestartObservation,
    SignalIndex,
    WorkloadSample,
    detect,
    workload_of,
)

T0 = 1_800_000_000.0


def _pod(name, created, first=None, last=None, workload="web", ns="apps", owner_created=None):
    return PodLife(ns, name, workload, created, first if first is not None else created, last if last is not None else T0 + 900, owner_created)


def _cfg():
    return DetectorConfig(observation_start=T0, observation_end=T0 + 900)


def test_workload_of_deployment_daemonset_statefulset_and_owner():
    assert workload_of("victim-kill-6cbc746867-fxnbp") == "victim-kill"
    assert workload_of("kindnet-hq484") == "kindnet"
    assert workload_of("qdrant-0") == "qdrant"
    assert workload_of("anything", owner_name="web-5c54c5cff", owner_kind="ReplicaSet") == "web"
    assert workload_of("db-0", owner_name="db", owner_kind="StatefulSet") == "db"


def test_pod_kill_shows_up_as_replacement_vanish_and_events():
    pods = [
        _pod("web-abc1234567-aaaaa", T0 - 3600, first=T0, last=T0 + 300),  # killed at ~300s
        _pod("web-abc1234567-bbbbb", T0 - 3600),
        _pod("web-abc1234567-ccccc", T0 + 301),  # replacement
    ]
    events = [
        LifecycleEvent(T0 + 300, "apps", "Pod", "web-abc1234567-aaaaa", "Killing", message="Stopping container web"),
        LifecycleEvent(T0 + 301, "apps", "ReplicaSet", "web-abc1234567", "SuccessfulCreate"),
    ]
    kinds = {s.kind for s in detect(events=events, pods=pods, config=_cfg())}
    assert kinds == {"pod_replaced", "pod_vanished", "event:Killing", "event:SuccessfulCreate"}


def test_scale_up_and_scale_down_are_not_disruptions():
    pods = [
        _pod("web-abc1234567-aaaaa", T0 - 3600),
        _pod("web-abc1234567-ccccc", T0 + 301),  # created by the scale-up
        _pod("web-abc1234567-ddddd", T0 + 200, last=T0 + 600),  # removed by a scale-down
    ]
    events = [
        LifecycleEvent(T0 + 300, "apps", "Deployment", "web", "ScalingReplicaSet", message="Scaled up"),
        LifecycleEvent(T0 + 200, "apps", "Deployment", "web", "ScalingReplicaSet", message="Scaled up"),
        LifecycleEvent(T0 + 600, "apps", "Deployment", "web", "ScalingReplicaSet", message="Scaled down"),
        LifecycleEvent(T0 + 601, "apps", "Pod", "web-abc1234567-ddddd", "Killing"),
        LifecycleEvent(T0 + 301, "apps", "ReplicaSet", "web-abc1234567", "SuccessfulCreate"),
    ]
    assert detect(events=events, pods=pods, config=_cfg()) == []


def test_desired_change_in_metrics_also_explains_churn():
    samples = [
        WorkloadSample(T0, "apps", "web", desired=0, available=0),
        WorkloadSample(T0 + 15, "apps", "web", desired=2, available=0),  # scaled 0 -> 2
        WorkloadSample(T0 + 30, "apps", "web", desired=2, available=2),
        WorkloadSample(T0 + 400, "apps", "web", desired=2, available=1),  # real dip
    ]
    signals = detect(workloads=samples, config=_cfg())
    assert [(s.kind, s.ts) for s in signals] == [("replicas_unavailable", T0 + 400)]
    assert signals[0].detail == "1/2 available"


def test_pods_present_at_start_or_at_end_are_not_signals():
    pods = [
        _pod("web-abc1234567-aaaaa", T0 - 3600),
        _pod("web-abc1234567-bbbbb", T0 - 3500),  # younger sibling, but created before observation
        _pod("web-abc1234567-ccccc", T0 - 3600, last=T0 + 880),  # series ends with the export
    ]
    assert detect(pods=pods, config=_cfg()) == []


def test_live_pod_younger_than_its_replicaset_is_a_replacement():
    pods = [_pod("web-abc1234567-ccccc", T0 + 100, owner_created=T0 - 86400)]
    signals = detect(pods=pods, config=DetectorConfig(observation_start=T0))
    assert [s.kind for s in signals] == ["pod_replaced"]


def test_restarts_and_warning_events_are_signals():
    restarts = [RestartObservation(T0 + 50, "apps", "web-abc1234567-aaaaa", "web", 1, "OOMKilled")]
    events = [
        LifecycleEvent(T0 + 60, "apps", "Pod", "web-abc1234567-aaaaa", "BackOff", type="Warning"),
        LifecycleEvent(T0 + 60, "apps", "Pod", "web-abc1234567-aaaaa", "Pulled"),  # routine
        LifecycleEvent(T0 + 60, "apps", "PodChaos", "kill", "Killing"),  # not a Pod/ReplicaSet
    ]
    signals = detect(events=events, restarts=restarts, config=_cfg())
    assert [(s.kind, s.detail) for s in signals] == [
        ("container_restarted", "+1 (OOMKilled)"),
        ("event:BackOff", ""),
    ]


def test_signal_index_matches_workload_signals_inside_the_window():
    pods = [_pod("web-abc1234567-aaaaa", T0 - 3600, last=T0 + 300), _pod("web-abc1234567-bbbbb", T0 - 3600)]
    signals = detect(pods=pods, config=_cfg())
    index = SignalIndex(signals, {(p.namespace, p.pod): p.workload for p in pods})
    # The surviving sibling's window sees its workload's pod kill ...
    assert [s.kind for s in index.for_window("apps", "web-abc1234567-bbbbb", T0 + 200, T0 + 380)] == ["pod_vanished"]
    # ... a window that ends before it, or one that starts well after it, does not.
    assert index.for_window("apps", "web-abc1234567-bbbbb", T0 + 100, T0 + 280) == []
    assert index.for_window("apps", "web-abc1234567-bbbbb", T0 + 330, T0 + 510) == []
    # Other namespaces / workloads are independent.
    assert index.for_window("other", "web-abc1234567-bbbbb", T0 + 200, T0 + 380) == []
