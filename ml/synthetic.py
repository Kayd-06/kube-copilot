"""Generate labelled synthetic pod metric windows for training and evaluation.

The generator is the reason Phase 3 can be trained on a laptop with no cluster
and no Prometheus. Each series is a single pod sampled every `step_seconds`.
Windows are sliced from those series and labelled by the behaviour of the
whole series (normal, memory_leak, cpu_spike, restart_storm).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ml.features import MIB, MIN_WINDOW_POINTS, MetricPoint, extract_features

LABEL_NORMAL = "normal"
LABEL_MEMORY_LEAK = "memory_leak"
LABEL_CPU_SPIKE = "cpu_spike"
LABEL_RESTART_STORM = "restart_storm"
ANOMALY_LABELS = (LABEL_MEMORY_LEAK, LABEL_CPU_SPIKE, LABEL_RESTART_STORM)


@dataclass(frozen=True)
class WindowSample:
    points: list[MetricPoint]
    label: str
    pod: str

    @property
    def is_anomaly(self) -> bool:
        return self.label != LABEL_NORMAL

    def features(self):
        return extract_features(self.points)


def _clip(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def _series_times(n_points: int, step_seconds: int, start: float) -> np.ndarray:
    return start + np.arange(n_points, dtype=np.float64) * step_seconds


def generate_normal_series(
    rng: np.random.Generator,
    n_points: int,
    step_seconds: int,
    start: float = 1_700_000_000.0,
) -> list[MetricPoint]:
    times = _series_times(n_points, step_seconds, start)
    cpu_base = rng.uniform(0.03, 0.12)
    mem_base = rng.uniform(48, 96) * MIB  # MiB
    mem_limit = rng.choice([128.0, 192.0, 256.0]) * MIB
    rx = rng.uniform(8, 40) * MIB
    tx = rng.uniform(4, 20) * MIB
    rx_rate = rng.uniform(0.05, 0.4) * MIB / step_seconds  # bytes per second
    tx_rate = rng.uniform(0.02, 0.25) * MIB / step_seconds
    restarts = 0.0
    mem = mem_base
    points: list[MetricPoint] = []
    for i, ts in enumerate(times):
        cpu = _clip(cpu_base + rng.normal(0, 0.015) + 0.02 * np.sin(i / 8.0), 0.005, 0.35)
        # Healthy pods jitter a little, but their working set does not drift.
        mem = _clip(mem + rng.normal(0, 0.2 * MIB), mem_base - 3 * MIB, mem_base + 3 * MIB)
        # A rare single restart on an otherwise healthy pod.
        if i > 0 and rng.random() < 0.002:
            restarts += 1.0
        rx += rx_rate * step_seconds * rng.uniform(0.7, 1.3)
        tx += tx_rate * step_seconds * rng.uniform(0.7, 1.3)
        points.append(
            MetricPoint(
                ts=float(ts),
                cpu=cpu,
                memory_bytes=float(mem),
                memory_limit_bytes=float(mem_limit),
                restarts=restarts,
                net_rx_bytes=float(rx),
                net_tx_bytes=float(tx),
            )
        )
    return points


def generate_memory_leak_series(
    rng: np.random.Generator,
    n_points: int,
    step_seconds: int,
    start: float = 1_700_000_000.0,
) -> list[MetricPoint]:
    times = _series_times(n_points, step_seconds, start)
    cpu_base = rng.uniform(0.04, 0.14)
    mem = rng.uniform(40, 70) * MIB
    mem_limit = rng.choice([128.0, 192.0, 256.0]) * MIB
    # 2.5–6 MiB/min climbs clearly over a 3-minute window without instantly OOM-killing.
    leak_per_step = rng.uniform(2.5, 6.0) * MIB * (step_seconds / 60.0)
    onset = max(int(n_points * rng.uniform(0.1, 0.3)), 3)
    rx = rng.uniform(8, 30) * MIB
    tx = rng.uniform(4, 16) * MIB
    rx_rate = rng.uniform(0.05, 0.3) * MIB / step_seconds
    tx_rate = rng.uniform(0.02, 0.2) * MIB / step_seconds
    restarts = 0.0
    points: list[MetricPoint] = []
    for i, ts in enumerate(times):
        cpu = _clip(cpu_base + rng.normal(0, 0.02), 0.005, 0.4)
        if i >= onset:
            mem = mem + leak_per_step + rng.normal(0, 0.15 * MIB)
        else:
            mem = mem + rng.normal(0, 0.4 * MIB)
        mem = _clip(mem, 8 * MIB, mem_limit * 1.05)
        rx += rx_rate * step_seconds * rng.uniform(0.7, 1.3)
        tx += tx_rate * step_seconds * rng.uniform(0.7, 1.3)
        points.append(
            MetricPoint(
                ts=float(ts),
                cpu=cpu,
                memory_bytes=float(mem),
                memory_limit_bytes=float(mem_limit),
                restarts=restarts,
                net_rx_bytes=float(rx),
                net_tx_bytes=float(tx),
            )
        )
    return points


def generate_cpu_spike_series(
    rng: np.random.Generator,
    n_points: int,
    step_seconds: int,
    start: float = 1_700_000_000.0,
) -> list[MetricPoint]:
    times = _series_times(n_points, step_seconds, start)
    cpu_base = rng.uniform(0.04, 0.12)
    spike = rng.uniform(0.85, 1.8)
    onset = max(int(n_points * rng.uniform(0.25, 0.5)), 4)
    length = max(int(n_points * rng.uniform(0.25, 0.5)), 4)
    mem_base = rng.uniform(48, 96) * MIB
    mem_limit = rng.choice([128.0, 192.0, 256.0]) * MIB
    rx = rng.uniform(8, 40) * MIB
    tx = rng.uniform(4, 20) * MIB
    # Spikes often come with a burst of network too, but memory stays normal.
    rx_rate = rng.uniform(0.05, 0.4) * MIB / step_seconds
    tx_rate = rng.uniform(0.02, 0.25) * MIB / step_seconds
    mem = mem_base
    restarts = 0.0
    points: list[MetricPoint] = []
    for i, ts in enumerate(times):
        if onset <= i < onset + length:
            cpu = _clip(spike + rng.normal(0, 0.08), 0.6, 2.5)
            rx += rx_rate * 4 * step_seconds * rng.uniform(0.8, 1.4)
            tx += tx_rate * 4 * step_seconds * rng.uniform(0.8, 1.4)
        else:
            cpu = _clip(cpu_base + rng.normal(0, 0.015), 0.005, 0.3)
            rx += rx_rate * step_seconds * rng.uniform(0.7, 1.3)
            tx += tx_rate * step_seconds * rng.uniform(0.7, 1.3)
        mem = _clip(mem + rng.normal(0, 0.2 * MIB), mem_base - 3 * MIB, mem_base + 3 * MIB)
        points.append(
            MetricPoint(
                ts=float(ts),
                cpu=cpu,
                memory_bytes=float(mem),
                memory_limit_bytes=float(mem_limit),
                restarts=restarts,
                net_rx_bytes=float(rx),
                net_tx_bytes=float(tx),
            )
        )
    return points


def generate_restart_storm_series(
    rng: np.random.Generator,
    n_points: int,
    step_seconds: int,
    start: float = 1_700_000_000.0,
) -> list[MetricPoint]:
    times = _series_times(n_points, step_seconds, start)
    cpu_base = rng.uniform(0.02, 0.1)
    mem_base = rng.uniform(32, 80) * MIB
    mem_limit = rng.choice([128.0, 192.0, 256.0]) * MIB
    onset = max(int(n_points * rng.uniform(0.2, 0.45)), 4)
    rx = rng.uniform(4, 20) * MIB
    tx = rng.uniform(2, 12) * MIB
    rx_rate = rng.uniform(0.02, 0.2) * MIB / step_seconds
    tx_rate = rng.uniform(0.01, 0.12) * MIB / step_seconds
    restarts = float(rng.integers(0, 3))
    mem = mem_base
    # CrashLoop-like: a restart every 1–2 scrapes after onset.
    every = int(rng.choice([1, 2]))
    points: list[MetricPoint] = []
    for i, ts in enumerate(times):
        if i >= onset and (i - onset) % every == 0:
            restarts += 1.0
            # A restart often shows a brief CPU blip and a memory drop.
            cpu = _clip(cpu_base + rng.uniform(0.1, 0.35), 0.01, 0.6)
            mem = _clip(mem_base * rng.uniform(0.6, 0.9), 8 * MIB, mem_limit)
        else:
            cpu = _clip(cpu_base + rng.normal(0, 0.02), 0.005, 0.3)
            mem = _clip(mem + rng.normal(0, 0.2 * MIB), 8 * MIB, mem_base + 3 * MIB)
        rx += rx_rate * step_seconds * rng.uniform(0.5, 1.4)
        tx += tx_rate * step_seconds * rng.uniform(0.5, 1.4)
        points.append(
            MetricPoint(
                ts=float(ts),
                cpu=cpu,
                memory_bytes=float(mem),
                memory_limit_bytes=float(mem_limit),
                restarts=restarts,
                net_rx_bytes=float(rx),
                net_tx_bytes=float(tx),
            )
        )
    return points


_GENERATORS = {
    LABEL_NORMAL: generate_normal_series,
    LABEL_MEMORY_LEAK: generate_memory_leak_series,
    LABEL_CPU_SPIKE: generate_cpu_spike_series,
    LABEL_RESTART_STORM: generate_restart_storm_series,
}


def _windows_from_series(
    series: list[MetricPoint],
    label: str,
    pod: str,
    window_size: int,
    stride: int,
) -> list[WindowSample]:
    if len(series) < max(window_size, MIN_WINDOW_POINTS):
        return []
    samples: list[WindowSample] = []
    for start in range(0, len(series) - window_size + 1, stride):
        chunk = series[start : start + window_size]
        # For anomalies, skip windows that are still in the healthy prefix so
        # the label actually matches the window contents.
        if label == LABEL_MEMORY_LEAK:
            minutes = max((chunk[-1].ts - chunk[0].ts) / 60.0, 1.0 / 60.0)
            slope = ((chunk[-1].memory_bytes - chunk[0].memory_bytes) / MIB) / minutes
            if slope < 2.0:  # less than 2 MiB/min growth — still looks healthy
                continue
        elif label == LABEL_CPU_SPIKE:
            if max(p.cpu for p in chunk) < 0.6:
                continue
        elif label == LABEL_RESTART_STORM:
            if (chunk[-1].restarts - chunk[0].restarts) < 2:
                continue
        samples.append(WindowSample(points=chunk, label=label, pod=pod))
    return samples


def generate_dataset(
    n_normal_series: int = 80,
    n_anomaly_series: int = 24,
    series_length: int = 48,
    window_size: int = 12,
    stride: int = 4,
    step_seconds: int = 15,
    seed: int = 42,
) -> list[WindowSample]:
    """Build a labelled set of windows.

    Defaults produce a few thousand windows in well under a second, which is
    enough to train Isolation Forest and still cheap enough for unit tests
    (pass smaller counts).
    """
    rng = np.random.default_rng(seed)
    samples: list[WindowSample] = []

    for i in range(n_normal_series):
        series = generate_normal_series(rng, series_length, step_seconds, start=1_700_000_000.0 + i * 10_000)
        samples.extend(
            _windows_from_series(series, LABEL_NORMAL, f"normal-{i}", window_size, stride)
        )

    per_kind = 0 if n_anomaly_series <= 0 else max(n_anomaly_series // 3, 1)
    for label, count in (
        (LABEL_MEMORY_LEAK, per_kind),
        (LABEL_CPU_SPIKE, per_kind),
        (LABEL_RESTART_STORM, per_kind),
    ):
        gen = _GENERATORS[label]
        for i in range(count):
            series = gen(rng, series_length, step_seconds, start=1_800_000_000.0 + i * 10_000)
            samples.extend(_windows_from_series(series, label, f"{label}-{i}", window_size, stride))

    return samples
