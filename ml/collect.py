"""Run a scripted chaos timeline on a kind cluster and collect labelled metrics.

Usage (from the repo root, after `make cluster-up metrics-up chaos-up`):

    PYTHONPATH=. python -m ml.collect                     # full ~60 min run
    PYTHONPATH=. python -m ml.collect --dry-run           # print the plan only
    PYTHONPATH=. python -m ml.collect --query-only ml/data/real/<run>/timeline.json
    make collect

The timeline is: a baseline period with no chaos, then for each scenario a
chaos window followed by a recovery period. Every phase boundary is written to
`timeline.json` as it happens, so a run interrupted halfway can still be
queried with `--query-only`.

After the timeline, the collector queries the Prometheus range API with the
same PromQL as the live API (ml/promql.py) and writes one row per
(pod, timestamp) to `points.csv.gz` with a `label` column:

- `normal`            healthy pod, or any pod outside a chaos window
- `<scenario>`        a target pod while its scenario's chaos was active
- `recovery`          a target pod within `--grace-seconds` after chaos ended
- `injection_failed`  a target pod during a window whose chaos never applied

Windows are built from these rows at training time (ml/real_data.py).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import gzip
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ml.promql import REQUIRED_CHANNELS, pod_queries

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAOS_DIR = REPO_ROOT / "demo" / "chaos"
EXPERIMENTS_DIR = CHAOS_DIR / "experiments"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "ml" / "data" / "real"
CHAOS_NAMESPACE = "chaos-lab"

LABEL_NORMAL = "normal"
LABEL_RECOVERY = "recovery"
LABEL_INJECTION_FAILED = "injection_failed"


@dataclass(frozen=True)
class Scenario:
    name: str
    manifest: str  # file under demo/chaos/experiments
    target_prefix: str  # pod names of the target deployment start with this
    kind: str = "chaos"  # "chaos" = kubectl apply/delete; "scale" = scale a deployment
    deployment: str = ""  # for kind == "scale"
    verify: bool = True  # check Chaos Mesh AllInjected after starting


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("pod_kill", "pod-kill.yaml", "victim-kill-", verify=False),
        Scenario("cpu_stress", "cpu-stress.yaml", "victim-cpu-"),
        Scenario("memory_stress", "memory-stress.yaml", "victim-mem-"),
        Scenario(
            "memory_leak",
            "memory-leak.yaml",
            "victim-leak-",
            kind="scale",
            deployment="victim-leak",
            verify=False,
        ),
        Scenario("network_delay", "network-delay.yaml", "victim-net-"),
        Scenario("restart_storm", "restart-storm.yaml", "victim-restart-", verify=False),
    )
}
DEFAULT_SCENARIO_ORDER = (
    "cpu_stress",
    "memory_leak",
    "restart_storm",
    "memory_stress",
    "pod_kill",
    "network_delay",
)


@dataclass
class Phase:
    """One contiguous period of the timeline (unix seconds, end exclusive)."""

    kind: str  # baseline | chaos | recovery
    start: float
    end: float
    scenario: str | None = None
    target_prefix: str | None = None
    injected: bool | None = None  # None = not verified
    note: str = ""


@dataclass
class Timeline:
    run_id: str
    namespace: str
    step_seconds: int
    phases: list[Phase] = field(default_factory=list)

    @property
    def start(self) -> float:
        return min(p.start for p in self.phases)

    @property
    def end(self) -> float:
        return max(p.end for p in self.phases)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Timeline:
        raw = json.loads(text)
        phases = [Phase(**p) for p in raw.pop("phases")]
        return cls(phases=phases, **raw)


# ---------------------------------------------------------------- labelling


def label_point(namespace: str, pod: str, ts: float, timeline: Timeline, grace_seconds: float) -> str:
    """Label one (pod, timestamp) sample from the timeline."""
    if namespace != timeline.namespace:
        return LABEL_NORMAL
    for phase in timeline.phases:
        if phase.kind != "chaos" or not phase.target_prefix:
            continue
        if not pod.startswith(phase.target_prefix):
            continue
        if phase.start <= ts < phase.end:
            if phase.injected is False:
                return LABEL_INJECTION_FAILED
            return phase.scenario or LABEL_NORMAL
        if phase.end <= ts < phase.end + grace_seconds:
            return LABEL_RECOVERY if phase.injected is not False else LABEL_INJECTION_FAILED
    return LABEL_NORMAL


def phase_at(ts: float, timeline: Timeline) -> str:
    for phase in timeline.phases:
        if phase.start <= ts < phase.end:
            return phase.kind if phase.kind == "baseline" else f"{phase.kind}:{phase.scenario}"
    return "outside"


# ---------------------------------------------------------------- prometheus

POINT_COLUMNS = (
    "ts",
    "time_utc",
    "namespace",
    "pod",
    "cpu",
    "memory_bytes",
    "memory_limit_bytes",
    "restarts",
    "net_rx_bytes_per_s",
    "net_tx_bytes_per_s",
    "phase",
    "label",
)


class PrometheusError(RuntimeError):
    pass


def query_range(
    client: httpx.Client, base_url: str, expr: str, start: float, end: float, step: int
) -> list[dict]:
    try:
        response = client.get(
            f"{base_url.rstrip('/')}/api/v1/query_range",
            params={"query": expr, "start": start, "end": end, "step": step},
        )
    except httpx.HTTPError as exc:
        raise PrometheusError(f"Could not reach Prometheus at {base_url}: {exc}") from exc
    if response.status_code != 200:
        raise PrometheusError(f"Prometheus returned HTTP {response.status_code}: {response.text[:200]}")
    payload = response.json()
    if payload.get("status") != "success":
        raise PrometheusError(f"Prometheus query failed: {payload.get('error')}")
    return list((payload.get("data") or {}).get("result") or [])


def fetch_channels(
    client: httpx.Client,
    base_url: str,
    start: float,
    end: float,
    step: int,
    chunk_seconds: int = 3600,
) -> dict[tuple[str, str], dict[str, dict[int, float]]]:
    """Return {(namespace, pod): {channel: {ts: value}}} over [start, end]."""
    series: dict[tuple[str, str], dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for channel, expr in pod_queries().items():
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + chunk_seconds, end)
            try:
                results = query_range(client, base_url, expr, chunk_start, chunk_end, step)
            except PrometheusError:
                if channel in REQUIRED_CHANNELS:
                    raise
                print(f"warning: optional channel {channel} unavailable", file=sys.stderr)
                results = []
            for item in results:
                metric = item.get("metric") or {}
                ns, pod = metric.get("namespace"), metric.get("pod")
                if not ns or not pod:
                    continue
                for ts, value in item.get("values") or []:
                    try:
                        series[(ns, pod)][channel][int(float(ts))] = float(value)
                    except (TypeError, ValueError):
                        continue
            chunk_start = chunk_end + step
    return series


def build_rows(
    channels: dict[tuple[str, str], dict[str, dict[int, float]]],
    timeline: Timeline,
    grace_seconds: float,
) -> list[dict]:
    """Flatten channels into labelled rows. A row needs cpu or memory."""
    rows: list[dict] = []
    for (ns, pod), chans in sorted(channels.items()):
        timestamps = sorted({ts for chan in chans.values() for ts in chan})
        for ts in timestamps:
            cpu = chans.get("cpu", {}).get(ts)
            mem = chans.get("memory", {}).get(ts)
            if cpu is None and mem is None:
                continue
            rows.append(
                {
                    "ts": ts,
                    "time_utc": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "namespace": ns,
                    "pod": pod,
                    "cpu": float(cpu or 0.0),
                    "memory_bytes": float(mem or 0.0),
                    "memory_limit_bytes": float(chans.get("limit", {}).get(ts, 0.0)),
                    "restarts": float(chans.get("restarts", {}).get(ts, 0.0)),
                    "net_rx_bytes_per_s": float(chans.get("rx", {}).get(ts, 0.0)),
                    "net_tx_bytes_per_s": float(chans.get("tx", {}).get(ts, 0.0)),
                    "phase": phase_at(ts, timeline),
                    "label": label_point(ns, pod, ts, timeline, grace_seconds),
                }
            )
    return rows


def write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(POINT_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------- cluster control

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def run_kubectl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=False)


class ChaosController:
    def __init__(self, runner: Runner = run_kubectl, namespace: str = CHAOS_NAMESPACE) -> None:
        self.run = runner
        self.namespace = namespace

    def _manifest(self, scenario: Scenario) -> str:
        return str(EXPERIMENTS_DIR / scenario.manifest)

    def prepare(self, scenarios: list[Scenario]) -> None:
        for scenario in scenarios:
            if scenario.kind == "scale":
                self._check(self.run(["apply", "-f", self._manifest(scenario)]))

    def start(self, scenario: Scenario) -> None:
        if scenario.kind == "scale":
            self._check(
                self.run(["-n", self.namespace, "scale", f"deploy/{scenario.deployment}", "--replicas=2"])
            )
        else:
            self._check(self.run(["apply", "-f", self._manifest(scenario)]))

    def stop(self, scenario: Scenario) -> None:
        if scenario.kind == "scale":
            self.run(["-n", self.namespace, "scale", f"deploy/{scenario.deployment}", "--replicas=0"])
        else:
            self.run(["delete", "-f", self._manifest(scenario), "--ignore-not-found", "--wait=false"])

    def verify(self, scenario: Scenario) -> tuple[bool | None, str]:
        """Return (injected, note). None means the scenario is not verifiable."""
        if not scenario.verify:
            return None, ""
        result = self.run(["get", "-f", self._manifest(scenario), "-o", "json"])
        if result.returncode != 0:
            return False, result.stderr.strip()[:200]
        try:
            obj = json.loads(result.stdout)
        except ValueError:
            return False, "unparseable kubectl output"
        conditions = {c.get("type"): c.get("status") for c in (obj.get("status") or {}).get("conditions") or []}
        injected = conditions.get("AllInjected") == "True"
        return injected, "" if injected else f"conditions={conditions}"

    @staticmethod
    def _check(result: subprocess.CompletedProcess) -> None:
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())


# ---------------------------------------------------------------- timeline


def plan(
    scenarios: list[str], baseline_s: float, chaos_s: float, recovery_s: float, start: float = 0.0
) -> list[Phase]:
    phases = [Phase("baseline", start, start + baseline_s)]
    t = start + baseline_s
    for name in scenarios:
        scenario = SCENARIOS[name]
        phases.append(Phase("chaos", t, t + chaos_s, name, scenario.target_prefix))
        t += chaos_s
        phases.append(Phase("recovery", t, t + recovery_s, name))
        t += recovery_s
    return phases


def run_timeline(
    timeline: Timeline,
    scenarios: list[str],
    baseline_s: float,
    chaos_s: float,
    recovery_s: float,
    controller: ChaosController,
    out_path: Path,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    verify_after_s: float = 30.0,
) -> Timeline:
    def save() -> None:
        out_path.write_text(timeline.to_json(), encoding="utf-8")

    def log(msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] {msg}", flush=True)

    controller.prepare([SCENARIOS[n] for n in scenarios])
    t0 = clock()
    log(f"baseline for {baseline_s / 60:.1f} min")
    sleep(baseline_s)
    timeline.phases.append(Phase("baseline", t0, clock()))
    save()

    for name in scenarios:
        scenario = SCENARIOS[name]
        start = clock()
        log(f"chaos {name} for {chaos_s / 60:.1f} min")
        injected: bool | None = None
        note = ""
        try:
            controller.start(scenario)
        except RuntimeError as exc:
            injected, note = False, str(exc)[:200]
            log(f"  start failed: {note}")
        if injected is None:
            wait = min(verify_after_s, chaos_s)
            sleep(wait)
            injected, note = controller.verify(scenario)
            if injected is False:
                log(f"  chaos did not inject: {note}")
            sleep(max(chaos_s - wait, 0.0))
        else:
            sleep(chaos_s)
        controller.stop(scenario)
        end = clock()
        timeline.phases.append(Phase("chaos", start, end, name, scenario.target_prefix, injected, note))
        save()
        log(f"recovery after {name} for {recovery_s / 60:.1f} min")
        sleep(recovery_s)
        timeline.phases.append(Phase("recovery", end, clock(), name))
        save()
    return timeline


# ---------------------------------------------------------------- port-forward


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@contextlib.contextmanager
def prometheus_port_forward(enabled: bool, port: int = 9090) -> Iterator[str]:
    url = f"http://localhost:{port}"
    if not enabled or _port_open(port):
        yield url
        return
    proc = subprocess.Popen(
        ["kubectl", "-n", "observability", "port-forward", "svc/prometheus", f"{port}:9090"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            if _port_open(port):
                break
            time.sleep(0.25)
        yield url
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


# ---------------------------------------------------------------- main


def summarize(rows: list[dict]) -> dict:
    by_label: dict[str, int] = defaultdict(int)
    pods = set()
    for row in rows:
        by_label[row["label"]] += 1
        pods.add((row["namespace"], row["pod"]))
    return {"rows": len(rows), "pods": len(pods), "rows_by_label": dict(sorted(by_label.items()))}


def query_and_write(timeline: Timeline, out_dir: Path, prometheus_url: str, grace_seconds: float) -> dict:
    # Pad the query so the first window of the baseline has history behind it.
    with httpx.Client(timeout=60.0) as client:
        channels = fetch_channels(
            client, prometheus_url, timeline.start, timeline.end, timeline.step_seconds
        )
    rows = build_rows(channels, timeline, grace_seconds)
    points_path = out_dir / "points.csv.gz"
    write_rows(rows, points_path)
    summary = summarize(rows)
    summary.update(
        {
            "run_id": timeline.run_id,
            "start_utc": datetime.fromtimestamp(timeline.start, timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(timeline.end, timezone.utc).isoformat(),
            "minutes": round((timeline.end - timeline.start) / 60.0, 1),
            "step_seconds": timeline.step_seconds,
            "grace_seconds": grace_seconds,
        }
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect labelled real metrics under chaos.")
    parser.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIO_ORDER))
    parser.add_argument("--skip", default="", help="comma-separated scenarios to leave out")
    parser.add_argument("--baseline-minutes", type=float, default=12.0)
    parser.add_argument("--chaos-minutes", type=float, default=5.0)
    parser.add_argument("--recovery-minutes", type=float, default=3.0)
    parser.add_argument("--grace-seconds", type=float, default=60.0)
    parser.add_argument("--step-seconds", type=int, default=15)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--prometheus-url",
        default=os.environ.get("PROMETHEUS_URL", ""),
        help="default: port-forward observability/prometheus to localhost:9090",
    )
    parser.add_argument("--query-only", type=Path, default=None, help="re-query an existing timeline.json")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    skip = {s for s in args.skip.split(",") if s}
    scenarios = [s for s in args.scenarios.split(",") if s and s not in skip]
    unknown = [s for s in scenarios if s not in SCENARIOS]
    if unknown:
        print(f"Unknown scenarios: {unknown}. Known: {sorted(SCENARIOS)}", file=sys.stderr)
        return 2
    baseline_s = args.baseline_minutes * 60
    chaos_s = args.chaos_minutes * 60
    recovery_s = args.recovery_minutes * 60

    if args.dry_run:
        for phase in plan(scenarios, baseline_s, chaos_s, recovery_s):
            print(f"{phase.start / 60:6.1f}-{phase.end / 60:6.1f} min  {phase.kind:9s} {phase.scenario or ''}")
        total = baseline_s + len(scenarios) * (chaos_s + recovery_s)
        print(f"total: {total / 60:.1f} min")
        return 0

    if args.query_only:
        timeline = Timeline.from_json(args.query_only.read_text(encoding="utf-8"))
        out_dir = args.query_only.parent
    else:
        if shutil.which("kubectl") is None:
            print("kubectl not found on PATH", file=sys.stderr)
            return 2
        run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%MZ")
        out_dir = args.output_root / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        timeline = Timeline(run_id=run_id, namespace=CHAOS_NAMESPACE, step_seconds=args.step_seconds)
        run_timeline(
            timeline,
            scenarios,
            baseline_s,
            chaos_s,
            recovery_s,
            ChaosController(),
            out_dir / "timeline.json",
        )

    with prometheus_port_forward(enabled=not args.prometheus_url) as default_url:
        summary = query_and_write(timeline, out_dir, args.prometheus_url or default_url, args.grace_seconds)
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_dir / 'points.csv.gz'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
