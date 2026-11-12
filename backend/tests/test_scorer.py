import numpy as np
import pytest

from app.anomaly.scorer import AnomalyModelMissingError, AnomalyScorer
from app.config import Settings
from ml.features import MetricPoint
from ml.synthetic import generate_cpu_spike_series, generate_memory_leak_series, generate_normal_series
from ml.train import train


def _scorer_from_train(tmp_path):
    artifact, _, _, _ = train(
        seed=1,
        n_estimators=40,
        n_normal_series=16,
        n_anomaly_series=12,
        series_length=36,
        stride=6,
        threshold_percentile=90,
    )
    path = tmp_path / "model.joblib"
    import joblib

    joblib.dump(artifact, path)
    settings = Settings(anomaly_model_path=str(path))
    return AnomalyScorer(settings=settings, artifact=artifact)


def test_missing_model_raises_clear_error(tmp_path):
    settings = Settings(anomaly_model_path=str(tmp_path / "does-not-exist.joblib"))
    scorer = AnomalyScorer(settings=settings)
    assert scorer.loaded is False
    assert "not found" in scorer.missing_message.lower() or "make train" in scorer.missing_message.lower()
    with pytest.raises(AnomalyModelMissingError, match="make train|not found"):
        scorer.require_loaded()


def test_scores_normal_lower_than_leak_and_spike(tmp_path):
    scorer = _scorer_from_train(tmp_path)
    rng = np.random.default_rng(7)
    normal = generate_normal_series(rng, n_points=16, step_seconds=15)[-12:]
    leak = generate_memory_leak_series(rng, n_points=24, step_seconds=15)[-12:]
    spike = generate_cpu_spike_series(rng, n_points=24, step_seconds=15)[-12:]

    normal_score = scorer.score_window("ns", "healthy", normal)
    leak_score = scorer.score_window("ns", "leaky", leak)
    spike_score = scorer.score_window("ns", "spiky", spike)

    assert normal_score.warning is None
    assert leak_score.score > normal_score.score
    assert spike_score.score > normal_score.score
    assert leak_score.is_anomaly or spike_score.is_anomaly
    assert leak_score.contributing_features
    names = {item["name"] for item in leak_score.contributing_features}
    # A leak should mention a memory feature among its top contributors.
    assert any("mem" in name for name in names)


def test_short_window_returns_warning(tmp_path):
    scorer = _scorer_from_train(tmp_path)
    point = MetricPoint(ts=1.0, cpu=0.1, memory_bytes=10, memory_limit_bytes=20, restarts=0, net_rx_bytes=0, net_tx_bytes=0)
    result = scorer.score_window("ns", "short", [point])
    assert result.is_anomaly is False
    assert result.warning and "Insufficient" in result.warning
