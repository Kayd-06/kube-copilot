"""Turn collected real-cluster points (ml/collect.py) into labelled windows.

A window is `window_size` consecutive samples of one pod. Its label comes from
the per-point labels written by the collector:

- `excluded`    any point is `injection_failed`
- `<scenario>`  at least half the points carry that chaos scenario label
- `transition`  some chaos/recovery points, but not enough for a scenario
- `normal`      every point is normal

`excluded` and `transition` windows are dropped from training and evaluation,
so ambiguous edges of a chaos window never count for or against the model.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ml.collect import LABEL_INJECTION_FAILED, LABEL_NORMAL, LABEL_RECOVERY, Timeline
from ml.features import MetricPoint

LABEL_TRANSITION = "transition"
LABEL_EXCLUDED = "excluded"
DROPPED_LABELS = frozenset({LABEL_TRANSITION, LABEL_EXCLUDED})


@dataclass(frozen=True)
class RealPoint:
    ts: float
    namespace: str
    pod: str
    cpu: float
    memory_bytes: float
    memory_limit_bytes: float
    restarts: float
    net_rx_bytes_per_s: float
    net_tx_bytes_per_s: float
    label: str


@dataclass(frozen=True)
class RealWindow:
    namespace: str
    pod: str
    points: list[MetricPoint]
    label: str
    start: float
    end: float  # timestamp of the last point

    @property
    def is_anomaly(self) -> bool:
        return self.label not in (LABEL_NORMAL, *DROPPED_LABELS)


def read_points(path: Path) -> list[RealPoint]:
    opener = gzip.open if path.suffix == ".gz" else open
    out: list[RealPoint] = []
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            out.append(
                RealPoint(
                    ts=float(row["ts"]),
                    namespace=row["namespace"],
                    pod=row["pod"],
                    cpu=float(row["cpu"]),
                    memory_bytes=float(row["memory_bytes"]),
                    memory_limit_bytes=float(row["memory_limit_bytes"]),
                    restarts=float(row["restarts"]),
                    net_rx_bytes_per_s=float(row["net_rx_bytes_per_s"]),
                    net_tx_bytes_per_s=float(row["net_tx_bytes_per_s"]),
                    label=row["label"],
                )
            )
    return out


def read_timeline(path: Path) -> Timeline:
    return Timeline.from_json(path.read_text(encoding="utf-8"))


def _segments(points: list[RealPoint], step_seconds: int) -> list[list[RealPoint]]:
    """Split a pod's samples wherever Prometheus has a gap (> 2 steps)."""
    segments: list[list[RealPoint]] = []
    current: list[RealPoint] = []
    for point in points:
        if current and point.ts - current[-1].ts > 2 * step_seconds:
            segments.append(current)
            current = []
        current.append(point)
    if current:
        segments.append(current)
    return segments


def to_metric_points(points: list[RealPoint], step_seconds: int) -> list[MetricPoint]:
    """Same conversion as the live API: network rates become cumulative bytes."""
    out: list[MetricPoint] = []
    rx_cum = tx_cum = 0.0
    prev_ts = points[0].ts if points else 0.0
    for point in points:
        dt = max(point.ts - prev_ts, step_seconds)
        rx_cum += point.net_rx_bytes_per_s * dt
        tx_cum += point.net_tx_bytes_per_s * dt
        out.append(
            MetricPoint(
                ts=point.ts,
                cpu=point.cpu,
                memory_bytes=point.memory_bytes,
                memory_limit_bytes=point.memory_limit_bytes,
                restarts=point.restarts,
                net_rx_bytes=rx_cum,
                net_tx_bytes=tx_cum,
            )
        )
        prev_ts = point.ts
    return out


def window_label(labels: list[str]) -> str:
    if LABEL_INJECTION_FAILED in labels:
        return LABEL_EXCLUDED
    counts = Counter(labels)
    scenario_counts = {
        label: n for label, n in counts.items() if label not in (LABEL_NORMAL, LABEL_RECOVERY)
    }
    if scenario_counts:
        label, n = max(scenario_counts.items(), key=lambda item: item[1])
        if n * 2 >= len(labels):
            return label
        return LABEL_TRANSITION
    if counts.get(LABEL_RECOVERY):
        return LABEL_TRANSITION
    return LABEL_NORMAL


def build_windows(
    points: list[RealPoint],
    window_size: int = 12,
    stride: int = 1,
    step_seconds: int = 15,
) -> list[RealWindow]:
    by_pod: dict[tuple[str, str], list[RealPoint]] = defaultdict(list)
    for point in points:
        by_pod[(point.namespace, point.pod)].append(point)
    windows: list[RealWindow] = []
    for (ns, pod), pod_points in sorted(by_pod.items()):
        pod_points.sort(key=lambda p: p.ts)
        for segment in _segments(pod_points, step_seconds):
            if len(segment) < window_size:
                continue
            metric_points = to_metric_points(segment, step_seconds)
            for start in range(0, len(segment) - window_size + 1, stride):
                chunk = segment[start : start + window_size]
                windows.append(
                    RealWindow(
                        namespace=ns,
                        pod=pod,
                        points=metric_points[start : start + window_size],
                        label=window_label([p.label for p in chunk]),
                        start=chunk[0].ts,
                        end=chunk[-1].ts,
                    )
                )
    return windows


def baseline_end(timeline: Timeline) -> float:
    ends = [p.end for p in timeline.phases if p.kind == "baseline"]
    if not ends:
        raise ValueError("timeline has no baseline phase")
    return max(ends)


def time_split(
    windows: list[RealWindow], split_ts: float
) -> tuple[list[RealWindow], list[RealWindow]]:
    """Train = normal windows that end before split_ts; test = windows that start at/after it.

    Windows that straddle the split are dropped so no sample is in both sets.
    Ambiguous (transition/excluded) windows are dropped from both.
    """
    train = [w for w in windows if w.end < split_ts and w.label == LABEL_NORMAL]
    test = [w for w in windows if w.start >= split_ts and w.label not in DROPPED_LABELS]
    return train, test


def load_run(run_dir: Path) -> tuple[list[RealPoint], Timeline]:
    points = read_points(run_dir / "points.csv.gz")
    timeline = read_timeline(run_dir / "timeline.json")
    return points, timeline


def window_counts(windows: list[RealWindow]) -> dict[str, int]:
    return dict(sorted(Counter(w.label for w in windows).items()))


def dump_counts(windows: list[RealWindow]) -> str:
    return json.dumps(window_counts(windows))
