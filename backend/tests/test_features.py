import numpy as np
import pytest

from ml.features import (
    FEATURE_NAMES,
    MIN_WINDOW_POINTS,
    MODEL_FEATURE_NAMES,
    MetricPoint,
    contributions,
    extract_feature_matrix,
    extract_features,
    select_model_features,
)


def _point(ts, cpu=0.1, mem=64 * 1024 * 1024, limit=128 * 1024 * 1024, restarts=0, rx=0, tx=0):
    return MetricPoint(
        ts=ts,
        cpu=cpu,
        memory_bytes=mem,
        memory_limit_bytes=limit,
        restarts=restarts,
        net_rx_bytes=rx,
        net_tx_bytes=tx,
    )


def test_feature_vector_length_and_names():
    window = [_point(1_700_000_000 + i * 15) for i in range(12)]
    features = extract_features(window)
    assert features.shape == (len(FEATURE_NAMES),)
    assert "mem_slope_mib_per_min" in FEATURE_NAMES
    assert set(MODEL_FEATURE_NAMES).issubset(FEATURE_NAMES)


def test_memory_leak_has_positive_slope_and_rising_ratio():
    start = 1_700_000_000
    window = []
    mem = 40 * 1024 * 1024
    for i in range(12):
        mem += 4 * 1024 * 1024  # 4 MiB every 15s ≈ 16 MiB/min
        window.append(_point(start + i * 15, mem=mem, limit=128 * 1024 * 1024))
    features = extract_features(window)
    named = dict(zip(FEATURE_NAMES, features, strict=True))
    assert named["mem_slope_mib_per_min"] > 10
    assert named["mem_to_limit_ratio"] == pytest.approx(window[-1].memory_bytes / window[-1].memory_limit_bytes)
    assert named["mem_zscore"] > 0


def test_restart_delta_and_rate():
    start = 1_700_000_000
    window = [_point(start + i * 15, restarts=i) for i in range(12)]  # +1 every step
    named = dict(zip(FEATURE_NAMES, extract_features(window), strict=True))
    assert named["restart_delta"] == 11
    assert named["restart_rate_per_min"] == pytest.approx(11 / 2.75, rel=0.05)


def test_zscore_is_zero_when_flat():
    window = [_point(1_700_000_000 + i * 15, cpu=0.2, mem=80 * 1024 * 1024) for i in range(8)]
    named = dict(zip(FEATURE_NAMES, extract_features(window), strict=True))
    assert named["cpu_zscore"] == 0
    assert named["mem_zscore"] == 0


def test_empty_window_raises():
    with pytest.raises(ValueError, match="empty"):
        extract_features([])


def test_short_window_still_returns_vector():
    features = extract_features([_point(1_700_000_000)])
    assert features.shape == (len(FEATURE_NAMES),)
    assert MIN_WINDOW_POINTS >= 2


def test_network_reset_does_not_go_negative():
    start = 1_700_000_000
    window = [
        _point(start, rx=1_000_000, tx=1_000_000),
        _point(start + 15, rx=100, tx=50),  # counter reset
        _point(start + 30, rx=200, tx=80),
    ]
    named = dict(zip(FEATURE_NAMES, extract_features(window), strict=True))
    assert named["net_rx_mib_per_min"] >= 0
    assert named["net_tx_mib_per_min"] >= 0


def test_feature_matrix_and_model_slice():
    windows = [
        [_point(1_700_000_000 + i * 15 + offset) for i in range(6)]
        for offset in (0, 1000)
    ]
    matrix = extract_feature_matrix(windows)
    assert matrix.shape == (2, len(FEATURE_NAMES))
    sliced = select_model_features(matrix)
    assert sliced.shape == (2, len(MODEL_FEATURE_NAMES))


def test_contributions_rank_by_abs_zscore():
    features = np.zeros(len(FEATURE_NAMES))
    features[FEATURE_NAMES.index("mem_slope_mib_per_min")] = 10
    features[FEATURE_NAMES.index("cpu_last")] = 2
    mean = np.zeros(len(FEATURE_NAMES))
    scale = np.ones(len(FEATURE_NAMES))
    top = contributions(features, mean, scale, top_k=2)
    assert top[0]["name"] == "mem_slope_mib_per_min"
    assert top[0]["direction"] == "high"
    assert top[1]["name"] == "cpu_last"
