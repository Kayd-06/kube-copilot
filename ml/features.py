"""Feature engineering over rolling windows of per-pod metrics.

The same functions are used to train the Isolation Forest and to score live
Prometheus samples, so the feature order and units stay locked together.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Higher-level units keep feature values readable in the UI.
MIB = 1024.0 * 1024.0

FEATURE_NAMES: tuple[str, ...] = (
    "cpu_last",
    "cpu_mean",
    "cpu_zscore",
    "cpu_slope",
    "mem_last_mib",
    "mem_mean_mib",
    "mem_zscore",
    "mem_slope_mib_per_min",
    "mem_to_limit_ratio",
    "restart_last",
    "restart_delta",
    "restart_rate_per_min",
    "net_rx_mib_per_min",
    "net_tx_mib_per_min",
)

# Isolation Forest isolates poorly when a rare behaviour only shows up in one or
# two of many features. The model is therefore trained on this smaller set of
# high-signal features; the full vector is still computed for UI contributions.
MODEL_FEATURE_NAMES: tuple[str, ...] = (
    "cpu_last",
    "cpu_mean",
    "mem_zscore",
    "mem_slope_mib_per_min",
    "mem_to_limit_ratio",
    "restart_delta",
    "restart_rate_per_min",
)

MIN_WINDOW_POINTS = 3


@dataclass(frozen=True)
class MetricPoint:
    """One sample of a pod's resource usage."""

    ts: float  # unix seconds
    cpu: float  # cores (already a rate, not a counter)
    memory_bytes: float
    memory_limit_bytes: float  # 0 if unknown
    restarts: float  # cumulative restart count
    net_rx_bytes: float  # cumulative bytes received
    net_tx_bytes: float  # cumulative bytes transmitted


def _safe_zscore(last: float, values: np.ndarray) -> float:
    std = float(np.std(values))
    if std < 1e-9:
        return 0.0
    return float((last - float(np.mean(values))) / std)


def _slope(times: np.ndarray, values: np.ndarray) -> float:
    """Least-squares slope (value units per second). Zero if the window is flat."""
    if times.size < 2:
        return 0.0
    t = times - times[0]
    denom = float(np.dot(t - t.mean(), t - t.mean()))
    if denom < 1e-12:
        return 0.0
    v = values
    return float(np.dot(t - t.mean(), v - v.mean()) / denom)


def _duration_minutes(times: np.ndarray) -> float:
    if times.size < 2:
        return 1.0 / 60.0
    return max((float(times[-1] - times[0])) / 60.0, 1.0 / 60.0)


def extract_features(window: list[MetricPoint]) -> np.ndarray:
    """Return a 1-D feature vector for a time-ordered window of metric points.

    Short windows are padded logically (rates become zero, z-scores become zero)
    so a caller can still score a pod that just appeared. At least one point is
    required.
    """
    if not window:
        raise ValueError("Cannot extract features from an empty window")

    points = sorted(window, key=lambda p: p.ts)
    times = np.array([p.ts for p in points], dtype=np.float64)
    cpu = np.array([p.cpu for p in points], dtype=np.float64)
    mem = np.array([p.memory_bytes for p in points], dtype=np.float64)
    restarts = np.array([p.restarts for p in points], dtype=np.float64)
    rx = np.array([p.net_rx_bytes for p in points], dtype=np.float64)
    tx = np.array([p.net_tx_bytes for p in points], dtype=np.float64)

    last = points[-1]
    mem_limit = max(float(last.memory_limit_bytes), 0.0)
    mem_ratio = float(last.memory_bytes / mem_limit) if mem_limit > 0 else 0.0
    # A ratio slightly over 1 can happen if the working set overshoots; clamp for stability.
    mem_ratio = float(min(max(mem_ratio, 0.0), 2.0))

    minutes = _duration_minutes(times)
    restart_delta = float(max(restarts[-1] - restarts[0], 0.0))

    # Network counters should be monotonic; a reset (pod restart) can go backwards.
    rx_delta = float(rx[-1] - rx[0]) if rx[-1] >= rx[0] else float(rx[-1])
    tx_delta = float(tx[-1] - tx[0]) if tx[-1] >= tx[0] else float(tx[-1])

    features = np.array(
        [
            float(cpu[-1]),
            float(np.mean(cpu)),
            _safe_zscore(float(cpu[-1]), cpu),
            _slope(times, cpu),
            float(mem[-1] / MIB),
            float(np.mean(mem) / MIB),
            _safe_zscore(float(mem[-1]), mem),
            _slope(times, mem / MIB) * 60.0,  # MiB per minute
            mem_ratio,
            float(restarts[-1]),
            restart_delta,
            restart_delta / minutes,
            (rx_delta / MIB) / minutes,
            (tx_delta / MIB) / minutes,
        ],
        dtype=np.float64,
    )
    # Guard against NaN/Inf from empty std or missing Prometheus values.
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
    return features


def extract_feature_matrix(windows: list[list[MetricPoint]]) -> np.ndarray:
    """Stack feature vectors into a (n_windows, n_features) array."""
    if not windows:
        return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float64)
    return np.vstack([extract_features(window) for window in windows])


def model_feature_indices() -> list[int]:
    return [FEATURE_NAMES.index(name) for name in MODEL_FEATURE_NAMES]


def select_model_features(matrix: np.ndarray) -> np.ndarray:
    """Slice the high-signal columns used by the Isolation Forest."""
    return matrix[:, model_feature_indices()]


def contributions(
    features: np.ndarray,
    mean: np.ndarray,
    scale: np.ndarray,
    top_k: int = 5,
) -> list[dict]:
    """Rank features by absolute z-score against the training distribution."""
    scale_safe = np.where(np.abs(scale) < 1e-12, 1.0, scale)
    zscores = (features - mean) / scale_safe
    items = []
    for name, value, zscore in zip(FEATURE_NAMES, features, zscores, strict=True):
        items.append(
            {
                "name": name,
                "value": float(value),
                "zscore": float(zscore),
                "direction": "high" if zscore >= 0 else "low",
            }
        )
    items.sort(key=lambda item: abs(item["zscore"]), reverse=True)
    return items[:top_k]
