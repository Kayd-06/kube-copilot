"""Event capture (ml/collect.py) and the offline loaders in ml/eval_lifecycle.py."""

import gzip
import json
import subprocess

from ml.collect import EventRecorder, normalize_event, read_events, write_lifecycle
from ml.eval_lifecycle import lifecycle_predictions, read_lifecycle, to_pods, to_restarts, to_workloads
from ml.lifecycle import SignalIndex, Signal
from ml.real_data import RealPoint, RealWindow


def _raw(kind="Pod", name="web-abc1234567-aaaaa", reason="Killing", uid="u1", count=1, last="2026-11-13T14:00:00Z"):
    return {
        "involvedObject": {"kind": kind, "name": name, "namespace": "apps"},
        "metadata": {"uid": uid, "namespace": "apps"},
        "reason": reason,
        "type": "Normal",
        "count": count,
        "firstTimestamp": "2026-11-13T13:59:00Z",
        "lastTimestamp": last,
        "message": "Stopping container web",
        "source": {"component": "kubelet"},
    }


def test_normalize_event_drops_chaos_mesh_objects():
    assert normalize_event(_raw(kind="PodChaos", name="pod-kill")) is None
    assert normalize_event(_raw(kind="Schedule", name="pod-kill")) is None
    event = normalize_event(_raw())
    assert event["kind"] == "Pod" and event["reason"] == "Killing" and event["source"] == "kubelet"
    assert event["last_ts"] - event["first_ts"] == 60


def test_event_recorder_dedupes_and_appends(tmp_path):
    batches = [
        [_raw(uid="a"), _raw(uid="b")],
        [_raw(uid="a"), _raw(uid="b", count=2, last="2026-11-13T14:01:00Z"), _raw(kind="PodChaos", uid="c")],
    ]

    def runner(args):
        items = batches.pop(0) if batches else []
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"items": items}), stderr="")

    path = tmp_path / "events.jsonl"
    recorder = EventRecorder(path, runner=runner)
    assert recorder.poll_once() == 2
    assert recorder.poll_once() == 1  # b's count went up; a unchanged; PodChaos skipped
    events = read_events(path)
    assert [(e["uid"], e["count"]) for e in events] == [("a", 1), ("b", 1), ("b", 2)]


def test_lifecycle_rows_round_trip_into_detector_inputs(tmp_path):
    rows = [
        {"ts": 100, "series": "pod_created", "namespace": "apps", "name": "web-abc1234567-aaaaa", "extra": "", "value": 50},
        {"ts": 115, "series": "pod_created", "namespace": "apps", "name": "web-abc1234567-aaaaa", "extra": "", "value": 50},
        {"ts": 100, "series": "pod_owner", "namespace": "apps", "name": "web-abc1234567-aaaaa", "extra": "web-abc1234567", "value": 1},
        {"ts": 100, "series": "deploy_desired", "namespace": "apps", "name": "web", "extra": "", "value": 2},
        {"ts": 100, "series": "deploy_available", "namespace": "apps", "name": "web", "extra": "", "value": 1},
        {"ts": 130, "series": "pod_last_terminated", "namespace": "apps", "name": "web-abc1234567-aaaaa", "extra": "OOMKilled", "value": 1},
    ]
    path = tmp_path / "lifecycle.csv.gz"
    write_lifecycle(rows, path)
    loaded = read_lifecycle(path)
    pods = to_pods(loaded)
    assert [(p.pod, p.workload, p.created_ts, p.first_seen, p.last_seen) for p in pods] == [
        ("web-abc1234567-aaaaa", "web", 50.0, 100.0, 115.0)
    ]
    samples = to_workloads(loaded)
    assert [(s.workload, s.desired, s.available) for s in samples] == [("web", 2.0, 1.0)]
    points = [
        RealPoint(t, "apps", "web-abc1234567-aaaaa", 0.1, 1.0, 2.0, r, 0, 0, "normal")
        for t, r in ((100, 0), (115, 0), (130, 1))
    ]
    restarts = to_restarts(points, loaded, {("apps", "web-abc1234567-aaaaa"): "web"})
    assert [(r.ts, r.delta, r.reason) for r in restarts] == [(130, 1, "OOMKilled")]
    with gzip.open(path, "rt") as handle:
        assert handle.readline().strip() == "ts,series,namespace,name,extra,value"


def test_lifecycle_predictions_flag_windows_with_workload_signals():
    windows = [
        RealWindow("apps", "web-abc1234567-bbbbb", [], "pod_kill", 100, 265),
        RealWindow("apps", "web-abc1234567-bbbbb", [], "normal", 400, 565),
        RealWindow("apps", "db-abc1234567-ccccc", [], "normal", 100, 265),
    ]
    index = SignalIndex([Signal(200, "apps", "web", "web-abc1234567-aaaaa", "pod_vanished")])
    pred, hits = lifecycle_predictions(windows, index)
    assert pred.tolist() == [1, 0, 0]
    assert hits[0][0].kind == "pod_vanished"
