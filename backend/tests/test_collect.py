"""ml/collect.py: timeline control, labelling, and Prometheus range parsing (mocked)."""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from ml import collect
from ml.collect import (
    ChaosController,
    Phase,
    PrometheusError,
    Timeline,
    build_rows,
    fetch_channels,
    label_point,
    plan,
    run_timeline,
)
from ml.promql import pod_queries

T0 = 1_800_000_000.0


def _timeline(injected: bool | None = True) -> Timeline:
    return Timeline(
        run_id="test",
        namespace="chaos-lab",
        step_seconds=15,
        phases=[
            Phase("baseline", T0, T0 + 600),
            Phase("chaos", T0 + 600, T0 + 900, "cpu_stress", "victim-cpu-", injected),
            Phase("recovery", T0 + 900, T0 + 1080, "cpu_stress"),
        ],
    )


def test_label_point_marks_targets_only_inside_their_window():
    tl = _timeline()
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 100, tl, 60) == "normal"
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 600, tl, 60) == "cpu_stress"
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 899, tl, 60) == "cpu_stress"
    # Just after the window: recovery for `grace` seconds, then normal again.
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 930, tl, 60) == "recovery"
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 960, tl, 60) == "normal"
    # Non-targets and other namespaces stay normal during chaos.
    assert label_point("chaos-lab", "steady-xyz", T0 + 700, tl, 60) == "normal"
    assert label_point("kube-system", "victim-cpu-abc", T0 + 700, tl, 60) == "normal"


def test_label_point_flags_failed_injection():
    tl = _timeline(injected=False)
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 700, tl, 60) == "injection_failed"
    assert label_point("chaos-lab", "victim-cpu-abc", T0 + 930, tl, 60) == "injection_failed"


def test_timeline_json_round_trip():
    tl = _timeline()
    again = Timeline.from_json(tl.to_json())
    assert again == tl
    assert again.start == T0 and again.end == T0 + 1080


def test_plan_lays_out_baseline_then_chaos_and_recovery():
    phases = plan(["cpu_stress", "pod_kill"], 600, 300, 120)
    assert [(p.kind, p.scenario) for p in phases] == [
        ("baseline", None),
        ("chaos", "cpu_stress"),
        ("recovery", "cpu_stress"),
        ("chaos", "pod_kill"),
        ("recovery", "pod_kill"),
    ]
    assert phases[-1].end == 600 + 2 * (300 + 120)
    assert phases[1].target_prefix == "victim-cpu-"


def test_every_scenario_manifest_exists():
    for scenario in collect.SCENARIOS.values():
        assert (collect.EXPERIMENTS_DIR / scenario.manifest).is_file(), scenario.manifest


def _matrix(pod: str, values: list[tuple[float, str]], ns: str = "chaos-lab") -> dict:
    return {"metric": {"namespace": ns, "pod": pod}, "values": [[ts, v] for ts, v in values]}


def _prometheus(responses: dict[str, list[dict]], fail: set[str] | None = None):
    """MockTransport that answers each channel's PromQL with canned matrices."""
    by_expr = {expr: channel for channel, expr in pod_queries().items()}
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        channel = by_expr[params["query"]]
        if fail and channel in fail:
            return httpx.Response(200, json={"status": "error", "error": "boom"})
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix", "result": responses.get(channel, [])}}
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_and_build_rows_labels_every_sample():
    ts = [T0 + 585, T0 + 600, T0 + 615]
    responses = {
        "cpu": [
            _matrix("victim-cpu-a", [(t, "0.8") for t in ts]),
            _matrix("steady-b", [(t, "0.001") for t in ts]),
        ],
        "memory": [_matrix("victim-cpu-a", [(t, str(10 * 2**20)) for t in ts])],
        "restarts": [_matrix("victim-cpu-a", [(ts[0], "0"), (ts[1], "1")])],
        "limit": [_matrix("victim-cpu-a", [(t, str(128 * 2**20)) for t in ts])],
        "rx": [_matrix("victim-cpu-a", [(t, "1000") for t in ts])],
        "tx": [],
    }
    client, seen = _prometheus(responses)
    channels = fetch_channels(client, "http://prom", T0, T0 + 620, 15)
    # One range query per channel, all at the requested step.
    assert {p["step"] for p in seen} == {"15"} and len(seen) == 6
    rows = build_rows(channels, _timeline(), grace_seconds=60)
    victim = [r for r in rows if r["pod"] == "victim-cpu-a"]
    assert [r["label"] for r in victim] == ["normal", "cpu_stress", "cpu_stress"]
    assert victim[1]["restarts"] == 1.0 and victim[2]["restarts"] == 0.0
    assert victim[0]["memory_limit_bytes"] == 128 * 2**20
    assert victim[0]["net_rx_bytes_per_s"] == 1000.0 and victim[0]["net_tx_bytes_per_s"] == 0.0
    assert victim[1]["phase"] == "chaos:cpu_stress" and victim[0]["phase"] == "baseline"
    steady = [r for r in rows if r["pod"] == "steady-b"]
    assert len(steady) == 3 and {r["label"] for r in steady} == {"normal"}


def test_fetch_channels_requires_cpu_and_memory_but_tolerates_optional():
    client, _ = _prometheus({"cpu": [_matrix("p", [(T0, "0.1")])], "memory": []}, fail={"rx"})
    channels = fetch_channels(client, "http://prom", T0, T0 + 30, 15)
    assert ("chaos-lab", "p") in channels

    client, _ = _prometheus({}, fail={"cpu"})
    with pytest.raises(PrometheusError):
        fetch_channels(client, "http://prom", T0, T0 + 30, 15)


def test_fetch_channels_chunks_long_ranges():
    client, seen = _prometheus({})
    fetch_channels(client, "http://prom", T0, T0 + 7200, 15, chunk_seconds=3600)
    cpu_calls = [p for p in seen if p["query"] == pod_queries()["cpu"]]
    assert [(float(p["start"]), float(p["end"])) for p in cpu_calls] == [(T0, T0 + 3600), (T0 + 3615, T0 + 7200)]


def test_write_rows_round_trips_through_gzip(tmp_path):
    client, _ = _prometheus({"cpu": [_matrix("victim-cpu-a", [(T0 + 600, "0.5")])]})
    rows = build_rows(fetch_channels(client, "http://prom", T0, T0 + 620, 15), _timeline(), 60)
    path = tmp_path / "points.csv.gz"
    collect.write_rows(rows, path)
    from ml.real_data import read_points

    points = read_points(path)
    assert len(points) == 1 and points[0].label == "cpu_stress" and points[0].cpu == 0.5


class FakeController(ChaosController):
    def __init__(self, inject_ok: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.inject_ok = inject_ok

        def runner(args: list[str]) -> subprocess.CompletedProcess:
            self.calls.append(("kubectl", " ".join(args)))
            if args[0] == "get":
                status = "True" if self.inject_ok else "False"
                body = {"status": {"conditions": [{"type": "AllInjected", "status": status}]}}
                return subprocess.CompletedProcess(args, 0, json.dumps(body), "")
            return subprocess.CompletedProcess(args, 0, "", "")

        super().__init__(runner=runner)


class FakeClock:
    def __init__(self):
        self.now = T0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize("inject_ok", [True, False])
def test_run_timeline_records_phases_and_injection(tmp_path, inject_ok):
    clock = FakeClock()
    controller = FakeController(inject_ok=inject_ok)
    tl = Timeline(run_id="t", namespace="chaos-lab", step_seconds=15)
    out = tmp_path / "timeline.json"
    run_timeline(
        tl, ["cpu_stress", "memory_leak"], 600, 300, 120, controller, out,
        sleep=clock.sleep, clock=clock.time, verify_after_s=30,
    )
    kinds = [(p.kind, p.scenario) for p in tl.phases]
    assert kinds == [
        ("baseline", None),
        ("chaos", "cpu_stress"),
        ("recovery", "cpu_stress"),
        ("chaos", "memory_leak"),
        ("recovery", "memory_leak"),
    ]
    assert tl.phases[1].start == T0 + 600 and tl.phases[1].end == T0 + 900
    assert tl.phases[1].injected is inject_ok
    assert tl.phases[3].injected is None  # scale-based scenario is not verifiable
    assert clock.now == T0 + 600 + 2 * 420
    commands = [c for _, c in controller.calls]
    assert any(c.startswith("apply -f") and "cpu-stress.yaml" in c for c in commands)
    assert any(c.startswith("delete -f") and "cpu-stress.yaml" in c for c in commands)
    assert "-n chaos-lab scale deploy/victim-leak --replicas=2" in commands
    assert "-n chaos-lab scale deploy/victim-leak --replicas=0" in commands
    saved = Timeline.from_json(out.read_text())
    assert saved == tl


def test_main_dry_run_prints_plan(capsys):
    assert collect.main(["--dry-run", "--skip", "network_delay", "--baseline-minutes", "1"]) == 0
    out = capsys.readouterr().out
    assert "network_delay" not in out and "cpu_stress" in out and "total:" in out


def test_main_rejects_unknown_scenario(capsys):
    assert collect.main(["--dry-run", "--scenarios", "nope"]) == 2
