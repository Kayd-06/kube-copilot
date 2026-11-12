from ml.train import save_artifact, save_report, train


def test_train_small_dataset_has_sane_metrics(tmp_path):
    artifact, report, train_normal, test = train(
        seed=0,
        n_estimators=40,
        n_normal_series=20,
        n_anomaly_series=12,
        series_length=36,
        window_size=12,
        stride=6,
        threshold_percentile=90,
    )
    assert train_normal
    assert test
    assert artifact["model"] is not None
    assert artifact["scaler"] is not None
    assert 0.0 <= report.precision <= 1.0
    assert 0.0 <= report.recall <= 1.0
    # On even a tiny synthetic set the signal is strong enough to beat chance.
    assert report.f1 >= 0.6
    assert report.n_train_normal == len(train_normal)
    path = tmp_path / "model.joblib"
    save_artifact(artifact, path)
    assert path.is_file() and path.stat().st_size > 0
    save_report(report, tmp_path / "m.json", tmp_path / "m.md")
    assert "F1" in (tmp_path / "m.md").read_text()
    assert (tmp_path / "m.json").read_text()
