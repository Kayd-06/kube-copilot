"""ml/real_data.py + ml/train_real.py on a tiny generated fixture (no cluster)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ml import train_real
from ml.collect import Phase, Timeline, label_point, write_rows
from ml.real_data import (
    LABEL_EXCLUDED,
    LABEL_TRANSITION,
    RealPoint,
    baseline_end,
    build_windows,
    time_split,
    to_metric_points,
    window_label,
)

T0 = 1_800_000_000.0
STEP = 15
MIB = 2**20


def _fixture_timeline() -> Timeline:
    return Timeline(
        run_id="fixture",
        namespace="chaos-lab",
        step_seconds=STEP,
        phases=[
            Phase("baseline", T0, T0 + 1200),
            Phase("chaos", T0 + 1200, T0 + 1500, "cpu_stress", "victim-cpu-", True),
            Phase("recovery", T0 + 1500, T0 + 1680, "cpu_stress"),
            Phase("chaos", T0 + 1680, T0 + 1980, "memory_stress", "victim-mem-", True),
            Phase("recovery", T0 + 1980, T0 + 2400, "memory_stress"),
        ],
    )


def _fixture_rows(timeline: Timeline, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    pods = [("chaos-lab", f"steady-{i}") for i in range(3)] + [
        ("chaos-lab", "victim-cpu-a"),
        ("chaos-lab", "victim-cpu-b"),
        ("chaos-lab", "victim-mem-a"),
        ("kube-system", "coredns-x"),
    ]
    for ns, pod in pods:
        base_mem = rng.uniform(6, 20) * MIB
        for ts in np.arange(T0, T0 + 2400, STEP):
            label = label_point(ns, pod, float(ts), timeline, 60)
            cpu = abs(rng.normal(0.002, 0.0005))
            mem = base_mem + rng.normal(0, 0.1 * MIB)
            if label == "cpu_stress":
                cpu = rng.uniform(0.7, 0.9)
            if label == "memory_stress":
                mem = base_mem + 96 * MIB
            rows.append(
                {
                    "ts": int(ts), "time_utc": "", "namespace": ns, "pod": pod, "cpu": cpu,
                    "memory_bytes": mem, "memory_limit_bytes": 128 * MIB, "restarts": 0.0,
                    "net_rx_bytes_per_s": 300.0, "net_tx_bytes_per_s": 500.0,
                    "phase": "", "label": label,
                }
            )
    return rows


@pytest.fixture
def run_dir(tmp_path):
    timeline = _fixture_timeline()
    d = tmp_path / "run-fixture"
    d.mkdir()
    write_rows(_fixture_rows(timeline), d / "points.csv.gz")
    (d / "timeline.json").write_text(timeline.to_json())
    (d / "summary.json").write_text(json.dumps({"run_id": "fixture", "minutes": 40, "rows": 1120, "pods": 7}))
    return d


def test_window_label_rules():
    assert window_label(["normal"] * 12) == "normal"
    assert window_label(["normal"] * 6 + ["cpu_stress"] * 6) == "cpu_stress"
    assert window_label(["normal"] * 7 + ["cpu_stress"] * 5) == LABEL_TRANSITION
    assert window_label(["normal"] * 11 + ["recovery"]) == LABEL_TRANSITION
    assert window_label(["cpu_stress"] * 11 + ["injection_failed"]) == LABEL_EXCLUDED


def _point(ts: float, label: str = "normal", pod: str = "p") -> RealPoint:
    return RealPoint(ts, "ns", pod, 0.01, 10 * MIB, 0.0, 0.0, 100.0, 50.0, label)


def test_to_metric_points_accumulates_network_rates():
    mp = to_metric_points([_point(T0), _point(T0 + 15), _point(T0 + 30)], STEP)
    assert [p.net_rx_bytes for p in mp] == [1500.0, 3000.0, 4500.0]
    assert [p.net_tx_bytes for p in mp] == [750.0, 1500.0, 2250.0]


def test_build_windows_splits_on_gaps_and_per_pod():
    points = [_point(T0 + i * STEP) for i in range(12)] + [_point(T0 + 1000 + i * STEP) for i in range(12)]
    points += [_point(T0 + i * STEP, pod="q") for i in range(11)]  # too short for a window
    windows = build_windows(points, window_size=12, stride=1, step_seconds=STEP)
    assert len(windows) == 2
    assert all(w.pod == "p" and len(w.points) == 12 for w in windows)
    assert windows[1].start == T0 + 1000


def test_time_split_has_no_overlap_or_leakage(run_dir):
    from ml.real_data import load_run

    points, timeline = load_run(run_dir)
    windows = build_windows(points, window_size=12, stride=1, step_seconds=STEP)
    split = baseline_end(timeline)
    train, test = time_split(windows, split)
    assert train and test
    assert all(w.end < split and w.label == "normal" for w in train)
    assert all(w.start >= split for w in test)
    assert not {(w.pod, w.start) for w in train} & {(w.pod, w.start) for w in test}
    assert {w.label for w in test} == {"normal", "cpu_stress", "memory_stress"}


def test_train_and_evaluate_detects_obvious_chaos(run_dir):
    from ml.real_data import load_run

    points, timeline = load_run(run_dir)
    windows = build_windows(points, window_size=12, stride=2, step_seconds=STEP)
    artifact, report = train_real.train_and_evaluate(
        windows, baseline_end(timeline), seed=0, n_estimators=50, include_synthetic=False
    )
    real = report["models"]["real"]
    assert "range_guard" in artifact  # the returned artifact is `real+guard`
    assert artifact["threshold"] == pytest.approx(real["threshold"])
    assert real["per_scenario"]["cpu_stress"]["recall"] >= 0.9
    assert real["per_scenario"]["memory_stress"]["recall"] >= 0.9
    assert real["f1"] >= 0.6
    assert set(report["threshold_sweep_real"]) == {"p90", "p95", "p99"}
    assert report["train_windows"] > 0 and "cpu_stress" in report["test_windows"]


def test_train_and_evaluate_requires_both_sides(run_dir):
    from ml.real_data import load_run

    points, timeline = load_run(run_dir)
    windows = build_windows(points, window_size=12, stride=4, step_seconds=STEP)
    with pytest.raises(RuntimeError):
        train_real.train_and_evaluate(windows, T0, include_synthetic=False)  # nothing before the split


def test_main_writes_reports_and_model(run_dir, tmp_path):
    out = tmp_path / "out"
    rc = train_real.main(
        [
            "--run-dir", str(run_dir), "--stride", "3", "--n-estimators", "30", "--no-mlflow",
            "--output", str(out / "m.joblib"), "--report-json", str(out / "r.json"),
            "--report-md", str(out / "r.md"),
        ]
    )
    assert rc == 0
    payload = json.loads((out / "r.json").read_text())
    assert set(payload) == {"in_run", "runs"}  # explicit --run-dir: no held-out run
    assert set(payload["in_run"]["models"]) == {
        "real", "real+guard", "synthetic", "synthetic+guard", "synthetic+real+guard"
    }
    md = (out / "r.md").read_text()
    assert "In-run time split" in md and "`cpu_stress`" in md and "`synthetic`" in md
    assert (out / "m.joblib").stat().st_size > 0


def test_main_scores_a_heldout_run(run_dir, tmp_path):
    later = tmp_path / "run-later"
    later.mkdir()
    timeline = _fixture_timeline()
    write_rows(_fixture_rows(timeline, seed=1), later / "points.csv.gz")
    (later / "timeline.json").write_text(timeline.to_json())
    out = tmp_path / "out2"
    rc = train_real.main(
        [
            "--run-dir", str(run_dir), "--heldout-run-dir", str(later), "--stride", "3",
            "--n-estimators", "30", "--no-mlflow", "--no-synthetic",
            "--output", str(out / "m.joblib"), "--report-json", str(out / "r.json"),
            "--report-md", str(out / "r.md"),
        ]
    )
    assert rc == 0
    payload = json.loads((out / "r.json").read_text())
    heldout = payload["heldout"]
    assert "held-out" in heldout["split"]
    # Every normal window of the training run is used, more than the in-run split.
    assert heldout["train_windows"] > payload["in_run"]["train_windows"]
    assert heldout["models"]["real+guard"]["per_scenario"]["cpu_stress"]["recall"] >= 0.9
    assert "Held-out run" in (out / "r.md").read_text()


def test_range_guard_flags_values_far_outside_training():
    from ml.features import FEATURE_NAMES
    from ml.features import fit_range_guard, range_guard_scores

    rng = np.random.default_rng(0)
    x = np.zeros((200, len(FEATURE_NAMES)))
    x[:, FEATURE_NAMES.index("cpu_last")] = rng.uniform(0.001, 0.01, 200)
    guard = fit_range_guard(x)
    probe = np.zeros((3, len(FEATURE_NAMES)))
    probe[:, FEATURE_NAMES.index("cpu_last")] = 0.005  # inside the training range
    probe[1, FEATURE_NAMES.index("cpu_last")] = 0.8  # far above the idle range
    probe[2, FEATURE_NAMES.index("restart_delta")] = 1.0  # one restart: at the floor, not flagged
    scores = range_guard_scores(guard, probe)
    assert scores[0] == 0.0
    assert scores[1] > guard["limit"]
    assert scores[2] <= guard["limit"]


def test_latest_run_dir(tmp_path, run_dir):
    assert train_real.latest_run_dir(run_dir.parent) == run_dir
    with pytest.raises(FileNotFoundError):
        train_real.latest_run_dir(tmp_path / "empty")
