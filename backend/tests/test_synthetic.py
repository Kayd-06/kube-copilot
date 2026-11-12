from ml.features import extract_features, FEATURE_NAMES
from ml.synthetic import (
    LABEL_CPU_SPIKE,
    LABEL_MEMORY_LEAK,
    LABEL_NORMAL,
    LABEL_RESTART_STORM,
    generate_dataset,
    generate_memory_leak_series,
)
import numpy as np


def test_dataset_contains_all_labels():
    samples = generate_dataset(
        n_normal_series=8,
        n_anomaly_series=9,
        series_length=36,
        window_size=12,
        stride=6,
        seed=0,
    )
    labels = {sample.label for sample in samples}
    assert labels == {LABEL_NORMAL, LABEL_MEMORY_LEAK, LABEL_CPU_SPIKE, LABEL_RESTART_STORM}
    assert any(not s.is_anomaly for s in samples)
    assert any(s.is_anomaly for s in samples)


def test_memory_leak_windows_have_rising_memory():
    rng = np.random.default_rng(0)
    series = generate_memory_leak_series(rng, n_points=40, step_seconds=15)
    # The second half should be clearly above the first half.
    mid = len(series) // 2
    assert series[-1].memory_bytes > series[mid].memory_bytes > series[0].memory_bytes


def test_leak_windows_have_steep_slope():
    samples = generate_dataset(n_normal_series=4, n_anomaly_series=9, seed=1, stride=4)
    leaks = [s for s in samples if s.label == LABEL_MEMORY_LEAK]
    assert leaks
    for sample in leaks:
        named = dict(zip(FEATURE_NAMES, extract_features(sample.points), strict=True))
        assert named["mem_slope_mib_per_min"] >= 2.0


def test_restart_storm_windows_increment_restarts():
    samples = generate_dataset(n_normal_series=4, n_anomaly_series=9, seed=2, stride=4)
    storms = [s for s in samples if s.label == LABEL_RESTART_STORM]
    assert storms
    for sample in storms:
        assert sample.points[-1].restarts - sample.points[0].restarts >= 2


def test_cpu_spike_windows_reach_high_cpu():
    samples = generate_dataset(n_normal_series=4, n_anomaly_series=9, seed=3, stride=4)
    spikes = [s for s in samples if s.label == LABEL_CPU_SPIKE]
    assert spikes
    for sample in spikes:
        assert max(p.cpu for p in sample.points) >= 0.6


def test_normal_windows_are_stable():
    samples = generate_dataset(n_normal_series=10, n_anomaly_series=0, seed=4)
    assert samples
    assert all(s.label == LABEL_NORMAL for s in samples)
    slopes = []
    for sample in samples:
        named = dict(zip(FEATURE_NAMES, extract_features(sample.points), strict=True))
        slopes.append(abs(named["mem_slope_mib_per_min"]))
        assert named["restart_delta"] <= 1
        assert named["cpu_last"] < 0.5
    assert float(np.median(slopes)) < 0.5
