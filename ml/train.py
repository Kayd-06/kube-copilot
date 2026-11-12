"""Train the Isolation Forest baseline and write a model artifact + eval report.

Usage (from the repo root, with the backend virtualenv):

    PYTHONPATH=. python -m ml.train
    make train

The model is trained only on normal windows. A threshold is taken from the
training score distribution (default: 95th percentile) so evaluation can report
precision / recall / F1 on held-out normal + labelled anomaly windows.

MLflow tracking defaults to a local `./mlruns` file store. Set
MLFLOW_TRACKING_URI to override. Pass `--no-mlflow` to skip tracking entirely
(used by the unit tests).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.preprocessing import StandardScaler

from ml.features import FEATURE_NAMES, MODEL_FEATURE_NAMES, extract_feature_matrix, select_model_features
from ml.synthetic import ANOMALY_LABELS, LABEL_NORMAL, WindowSample, generate_dataset

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = REPO_ROOT / "backend" / "models" / "isolation_forest.joblib"
DEFAULT_REPORT_JSON = REPO_ROOT / "ml" / "reports" / "metrics.json"
DEFAULT_REPORT_MD = REPO_ROOT / "ml" / "reports" / "metrics.md"
DEFAULT_MLRUNS = REPO_ROOT / "mlruns"
ARTIFACT_VERSION = "1.0"


@dataclass
class EvalReport:
    model: str
    artifact_version: str
    seed: int
    trained_at: str
    n_estimators: int
    contamination: str | float
    threshold_percentile: float
    threshold: float
    n_train_normal: int
    n_test_normal: int
    n_test_anomaly: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    true_negatives: int
    false_positives: int
    false_negatives: int
    true_positives: int
    recall_by_label: dict[str, float]
    feature_names: list[str]
    notes: str


def _split_samples(
    samples: list[WindowSample], seed: int, test_normal_fraction: float = 0.25
) -> tuple[list[WindowSample], list[WindowSample]]:
    rng = np.random.default_rng(seed)
    normal = [s for s in samples if not s.is_anomaly]
    anomalies = [s for s in samples if s.is_anomaly]
    rng.shuffle(normal)
    n_test_normal = max(int(len(normal) * test_normal_fraction), 1)
    test_normal = normal[:n_test_normal]
    train_normal = normal[n_test_normal:]
    if not train_normal:
        raise RuntimeError("Not enough normal windows to train; generate a larger dataset.")
    return train_normal, test_normal + anomalies


def _raw_scores(model: IsolationForest, x_scaled: np.ndarray) -> np.ndarray:
    """Higher means more anomalous."""
    return -model.decision_function(x_scaled)


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | int]:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "true_negatives": int(tn),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_positives": int(tp),
    }


def _recall_by_label(
    test: list[WindowSample], y_pred: np.ndarray
) -> dict[str, float]:
    out: dict[str, float] = {}
    for label in (LABEL_NORMAL, *ANOMALY_LABELS):
        idx = [i for i, sample in enumerate(test) if sample.label == label]
        if not idx:
            out[label] = math.nan
            continue
        if label == LABEL_NORMAL:
            # Specificity: fraction of normal windows left unflagged.
            out[label] = float(np.mean(y_pred[idx] == 0))
        else:
            out[label] = float(np.mean(y_pred[idx] == 1))
    return out


def train(
    seed: int = 42,
    n_estimators: int = 200,
    max_samples: str | int = "auto",
    threshold_percentile: float = 90.0,
    n_normal_series: int = 120,
    n_anomaly_series: int = 36,
    series_length: int = 48,
    window_size: int = 12,
    stride: int = 3,
    step_seconds: int = 15,
) -> tuple[dict, EvalReport, list[WindowSample], list[WindowSample]]:
    samples = generate_dataset(
        n_normal_series=n_normal_series,
        n_anomaly_series=n_anomaly_series,
        series_length=series_length,
        window_size=window_size,
        stride=stride,
        step_seconds=step_seconds,
        seed=seed,
    )
    train_normal, test = _split_samples(samples, seed=seed)
    x_train_full = extract_feature_matrix([s.points for s in train_normal])
    x_test_full = extract_feature_matrix([s.points for s in test])
    x_train = select_model_features(x_train_full)
    x_test = select_model_features(x_test_full)
    y_test = np.array([1 if s.is_anomaly else 0 for s in test], dtype=np.int32)

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_test_scaled = scaler.transform(x_test)

    # Fit a scaler on the full vector too, so the API can rank contributing features.
    full_scaler = StandardScaler()
    full_scaler.fit(x_train_full)

    model = IsolationForest(
        n_estimators=n_estimators,
        max_samples=max_samples,
        max_features=1,
        contamination="auto",
        random_state=seed,
        n_jobs=1,
    )
    model.fit(x_train_scaled)

    train_raw = _raw_scores(model, x_train_scaled)
    threshold = float(np.percentile(train_raw, threshold_percentile))
    test_raw = _raw_scores(model, x_test_scaled)
    y_pred = (test_raw >= threshold).astype(np.int32)

    metrics = _metrics(y_test, y_pred)
    report = EvalReport(
        model="isolation_forest",
        artifact_version=ARTIFACT_VERSION,
        seed=seed,
        trained_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        n_estimators=n_estimators,
        contamination="auto",
        threshold_percentile=threshold_percentile,
        threshold=threshold,
        n_train_normal=int(len(train_normal)),
        n_test_normal=int(sum(1 for s in test if not s.is_anomaly)),
        n_test_anomaly=int(sum(1 for s in test if s.is_anomaly)),
        precision=float(metrics["precision"]),
        recall=float(metrics["recall"]),
        f1=float(metrics["f1"]),
        accuracy=float(metrics["accuracy"]),
        true_negatives=int(metrics["true_negatives"]),
        false_positives=int(metrics["false_positives"]),
        false_negatives=int(metrics["false_negatives"]),
        true_positives=int(metrics["true_positives"]),
        recall_by_label={k: float(v) for k, v in _recall_by_label(test, y_pred).items()},
        feature_names=list(MODEL_FEATURE_NAMES),
        notes=(
            "Trained on synthetic normal windows only. Threshold is the "
            f"{threshold_percentile:.0f}th percentile of training anomaly scores. "
            "recall_by_label['normal'] is specificity (1 - false-positive rate)."
        ),
    )
    artifact = {
        "model": model,
        "scaler": scaler,
        "full_scaler": full_scaler,
        "feature_names": list(FEATURE_NAMES),
        "model_feature_names": list(MODEL_FEATURE_NAMES),
        "threshold": threshold,
        "threshold_percentile": threshold_percentile,
        "train_raw_mean": float(np.mean(train_raw)),
        "train_raw_std": float(np.std(train_raw)),
        "window_size": window_size,
        "step_seconds": step_seconds,
        "version": ARTIFACT_VERSION,
        "seed": seed,
    }
    return artifact, report, train_normal, test


def render_markdown(report: EvalReport) -> str:
    by_label_rows = "\n".join(
        f"| `{label}` | {value:.3f} |"
        for label, value in report.recall_by_label.items()
    )
    return f"""# Isolation Forest evaluation

Trained at **{report.trained_at}** (UTC) on synthetic pod metric windows.
Seed `{report.seed}`, artifact version `{report.artifact_version}`.

## Overall (held-out test set)

| Metric | Value |
|--------|-------|
| Precision | {report.precision:.3f} |
| Recall | {report.recall:.3f} |
| F1 | {report.f1:.3f} |
| Accuracy | {report.accuracy:.3f} |

Confusion matrix: TN={report.true_negatives}, FP={report.false_positives},
FN={report.false_negatives}, TP={report.true_positives}.

Train windows (normal only): {report.n_train_normal}. Test windows:
{report.n_test_normal} normal + {report.n_test_anomaly} anomalous.
Threshold: {report.threshold:.4f} ({report.threshold_percentile:.0f}th percentile
of training scores).

## Per-behaviour recall

`normal` is specificity (share of healthy windows *not* flagged).

| Label | Recall |
|-------|--------|
{by_label_rows}

## Notes

{report.notes}
"""


def _log_mlflow(
    artifact: dict,
    report: EvalReport,
    model_path: Path,
    report_json: Path,
    tracking_uri: str,
) -> str:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    # MLflow 3.x requires an explicit opt-in for the local file store.
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("kube-copilot-anomaly")
    with mlflow.start_run(run_name=f"isolation-forest-{report.seed}") as run:
        mlflow.log_params(
            {
                "model": report.model,
                "n_estimators": report.n_estimators,
                "contamination": report.contamination,
                "threshold_percentile": report.threshold_percentile,
                "seed": report.seed,
                "n_train_normal": report.n_train_normal,
                "window_size": artifact["window_size"],
                "step_seconds": artifact["step_seconds"],
                "n_features": len(MODEL_FEATURE_NAMES),
            }
        )
        mlflow.log_metrics(
            {
                "precision": report.precision,
                "recall": report.recall,
                "f1": report.f1,
                "accuracy": report.accuracy,
                "threshold": report.threshold,
                "false_positives": report.false_positives,
                "false_negatives": report.false_negatives,
                **{
                    f"recall_{label}": value
                    for label, value in report.recall_by_label.items()
                    if not math.isnan(value)
                },
            }
        )
        # Log the joblib artifact rather than mlflow.sklearn.log_model: MLflow 3.x
        # serializes sklearn trees through skops and rejects IsolationForest by default.
        mlflow.log_artifact(str(model_path), artifact_path="model")
        mlflow.log_artifact(str(report_json), artifact_path="reports")
        return run.info.run_id


def save_artifact(artifact: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)


def save_report(report: EvalReport, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the Kube Copilot Isolation Forest.")
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--threshold-percentile", type=float, default=90.0)
    parser.add_argument("--n-normal-series", type=int, default=120)
    parser.add_argument("--n-anomaly-series", type=int, default=36)
    parser.add_argument("--no-mlflow", action="store_true")
    parser.add_argument(
        "--mlflow-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", f"file://{DEFAULT_MLRUNS}"),
    )
    parser.add_argument(
        "--model",
        choices=("isolation_forest", "lstm"),
        default="isolation_forest",
        help="lstm is optional and requires `pip install -r ml/requirements-lstm.txt`.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.model == "lstm":
        from ml.lstm import train_and_save_lstm

        return train_and_save_lstm(args)

    artifact, report, _, test = train(
        seed=args.seed,
        n_estimators=args.n_estimators,
        threshold_percentile=args.threshold_percentile,
        n_normal_series=args.n_normal_series,
        n_anomaly_series=args.n_anomaly_series,
    )
    save_artifact(artifact, args.output)
    save_report(report, args.report_json, args.report_md)

    run_id = None
    if not args.no_mlflow:
        try:
            run_id = _log_mlflow(artifact, report, args.output, args.report_json, args.mlflow_uri)
        except Exception as exc:  # noqa: BLE001 — training should still succeed
            print(f"MLflow logging failed (model was still saved): {exc}", file=sys.stderr)

    counts = Counter(s.label for s in test)
    print(f"Wrote model to {args.output}")
    print(f"Wrote report to {args.report_json} and {args.report_md}")
    print(
        f"Test composition: {dict(counts)} | "
        f"precision={report.precision:.3f} recall={report.recall:.3f} f1={report.f1:.3f}"
    )
    if run_id:
        print(f"MLflow run: {run_id}  (ui: make mlflow-ui)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
