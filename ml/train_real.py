"""Train and evaluate the Isolation Forest on real cluster metrics.

Usage (from the repo root, with the backend virtualenv):

    PYTHONPATH=. python -m ml.train_real                       # newest run under ml/data/real/
    PYTHONPATH=. python -m ml.train_real --run-dir ml/data/real/run-box-01
    make train-real

Methodology
-----------
- Windows: 12 consecutive 15s samples of one pod (3 minutes), like the live API.
- Time-based split: the model is fit only on *normal* windows that end before
  the baseline period finishes. Everything that starts after it (later normal
  windows plus every chaos window) is the test set. Windows that straddle the
  split, or the ambiguous edges of a chaos window, are dropped.
- The decision threshold is the Nth percentile of the *training* scores
  (default 90th, the same rule as the synthetic model), so the test set is
  never used to tune anything.
- Three models are scored on the same real test set:
  `real` (real normal windows), `synthetic` (the existing synthetic-trained
  model, retrained here with its defaults), and `synthetic+real`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ml.collect import DEFAULT_OUTPUT_ROOT, LABEL_NORMAL
from ml.features import extract_feature_matrix
from ml.real_data import RealWindow, baseline_end, build_windows, load_run, time_split, window_counts
from ml.train import REPO_ROOT, fit_isolation_forest, metrics, predict, raw_scores, save_artifact
from ml.train import train as train_synthetic

DEFAULT_MODEL_PATH = REPO_ROOT / "backend" / "models" / "isolation_forest_real.joblib"
DEFAULT_REPORT_JSON = REPO_ROOT / "ml" / "reports" / "real_metrics.json"
DEFAULT_REPORT_MD = REPO_ROOT / "ml" / "reports" / "real_metrics.md"
DEFAULT_MLRUNS = REPO_ROOT / "mlruns"
SWEEP_PERCENTILES = (90.0, 95.0, 99.0)


def latest_run_dir(root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    runs = sorted(p.parent for p in root.glob("*/points.csv.gz"))
    if not runs:
        raise FileNotFoundError(
            f"No collected runs under {root}. Run `make collect` first (see ml/data/real/README.md)."
        )
    return runs[-1]


def _round(value: float) -> float | None:
    return None if math.isnan(value) else round(float(value), 4)


def evaluate(artifact: dict, test: list[RealWindow], threshold: float | None = None) -> dict:
    """Overall + per-scenario metrics for one model on labelled windows."""
    x = extract_feature_matrix([w.points for w in test])
    y_true = np.array([1 if w.is_anomaly else 0 for w in test], dtype=np.int32)
    y_pred = predict(artifact, x, threshold)
    overall = metrics(y_true, y_pred)
    labels = np.array([w.label for w in test])
    normal_idx = labels == LABEL_NORMAL

    per_scenario: dict[str, dict] = {}
    for scenario in sorted(set(labels) - {LABEL_NORMAL}):
        idx = labels == scenario
        subset = idx | normal_idx  # this scenario vs all test normals
        sub = metrics(y_true[subset], y_pred[subset])
        per_scenario[scenario] = {
            "windows": int(idx.sum()),
            "detected": int(y_pred[idx].sum()),
            "recall": _round(float(np.mean(y_pred[idx] == 1))),
            "precision": _round(sub["precision"]),
            "f1": _round(sub["f1"]),
        }

    fp_by_namespace = Counter(w.namespace for w, p in zip(test, y_pred, strict=True) if p == 1 and not w.is_anomaly)
    normal_by_namespace = Counter(w.namespace for w in test if not w.is_anomaly)
    return {
        "threshold": float(artifact["threshold"] if threshold is None else threshold),
        "precision": _round(overall["precision"]),
        "recall": _round(overall["recall"]),
        "f1": _round(overall["f1"]),
        "accuracy": _round(overall["accuracy"]),
        "specificity": _round(float(np.mean(y_pred[normal_idx] == 0))) if normal_idx.any() else None,
        "confusion": {k: int(overall[k]) for k in ("true_negatives", "false_positives", "false_negatives", "true_positives")},
        "per_scenario": per_scenario,
        "false_positive_rate_by_namespace": {
            ns: round(fp_by_namespace.get(ns, 0) / n, 4) for ns, n in sorted(normal_by_namespace.items())
        },
    }


def train_and_evaluate(
    windows: list[RealWindow],
    split_ts: float,
    seed: int = 42,
    n_estimators: int = 200,
    threshold_percentile: float = 90.0,
    include_synthetic: bool = True,
) -> tuple[dict, dict]:
    """Return (real_artifact, report). Raises if the split leaves a side empty."""
    train, test = time_split(windows, split_ts)
    if not train:
        raise RuntimeError("No normal training windows before the split; collect a longer baseline.")
    if not any(w.is_anomaly for w in test):
        raise RuntimeError("No chaos windows in the test set; check the timeline and labels.")

    x_train = extract_feature_matrix([w.points for w in train])
    window_size = len(train[0].points)
    real_artifact = fit_isolation_forest(
        x_train, seed=seed, n_estimators=n_estimators, threshold_percentile=threshold_percentile,
        window_size=window_size,
    )
    models: dict[str, dict] = {"real": evaluate(real_artifact, test)}

    sweep = {}
    train_scores = raw_scores(real_artifact, x_train)
    for pct in SWEEP_PERCENTILES:
        cut = float(np.percentile(train_scores, pct))
        result = evaluate(real_artifact, test, threshold=cut)
        sweep[f"p{pct:.0f}"] = {k: result[k] for k in ("threshold", "precision", "recall", "f1", "specificity")}

    if include_synthetic:
        syn_artifact, _, syn_train, _ = train_synthetic(
            seed=seed, n_estimators=n_estimators, threshold_percentile=threshold_percentile
        )
        models["synthetic"] = evaluate(syn_artifact, test)
        x_syn = extract_feature_matrix([s.points for s in syn_train])
        combined = fit_isolation_forest(
            np.vstack([x_syn, x_train]), seed=seed, n_estimators=n_estimators,
            threshold_percentile=threshold_percentile, window_size=window_size,
        )
        models["synthetic+real"] = evaluate(combined, test)
        n_syn = len(syn_train)
    else:
        n_syn = 0

    report = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": seed,
        "n_estimators": n_estimators,
        "threshold_percentile": threshold_percentile,
        "window_size": window_size,
        "split_ts": split_ts,
        "split_utc": datetime.fromtimestamp(split_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "train_windows": len(train),
        "train_windows_synthetic": n_syn,
        "test_windows": window_counts(test),
        "all_windows": window_counts(windows),
        "models": models,
        "threshold_sweep_real": sweep,
    }
    return real_artifact, report


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_markdown(report: dict, run_meta: dict) -> str:
    models = report["models"]
    names = list(models)
    header = "| Metric | " + " | ".join(f"`{n}`" for n in names) + " |"
    sep = "|---" * (len(names) + 1) + "|"
    rows = [
        "| " + key.capitalize() + " | " + " | ".join(_fmt(models[n][key]) for n in names) + " |"
        for key in ("precision", "recall", "f1", "specificity")
    ]
    scenarios = sorted({s for m in models.values() for s in m["per_scenario"]})
    scen_rows = []
    for scenario in scenarios:
        real = models["real"]["per_scenario"].get(scenario, {})
        cells = [
            f"`{scenario}`",
            str(real.get("windows", 0)),
            _fmt(real.get("precision")),
            _fmt(real.get("recall")),
            _fmt(real.get("f1")),
        ]
        for other in names[1:]:
            cells.append(_fmt(models[other]["per_scenario"].get(scenario, {}).get("recall")))
        scen_rows.append("| " + " | ".join(cells) + " |")
    scen_header = "| Scenario | Windows | Precision | Recall | F1 | " + " | ".join(
        f"Recall (`{n}`)" for n in names[1:]
    ) + " |"
    scen_sep = "|---" * (5 + len(names) - 1) + "|"
    sweep_rows = "\n".join(
        f"| {k} | {v['threshold']:.4f} | {_fmt(v['precision'])} | {_fmt(v['recall'])} | {_fmt(v['f1'])} | {_fmt(v['specificity'])} |"
        for k, v in report["threshold_sweep_real"].items()
    )
    fp_rows = "\n".join(
        f"| `{ns}` | " + " | ".join(_fmt(models[n]["false_positive_rate_by_namespace"].get(ns)) for n in names) + " |"
        for ns in models["real"]["false_positive_rate_by_namespace"]
    )
    c = models["real"]["confusion"]
    return f"""# Isolation Forest on real cluster metrics

Trained at **{report['trained_at']}** (UTC) from run `{run_meta.get('run_id')}`:
{run_meta.get('minutes')} minutes of Prometheus data from a single-node kind
cluster ({run_meta.get('rows')} pod samples, {run_meta.get('pods')} pods), with
Chaos Mesh experiments injected on a schedule. See `ml/data/real/README.md`.

## Method

- Window = {report['window_size']} samples x 15s = 3 minutes of one pod (same as the live API).
- **Time-based split, no leakage.** Train on normal windows that end before
  {report['split_utc']} (end of the no-chaos baseline). Test on every window that
  starts after it. Straddling windows and ambiguous chaos edges are dropped.
- Threshold = {report['threshold_percentile']:.0f}th percentile of *training* scores; nothing is tuned on the test set.
- Train windows: {report['train_windows']} real normal
  (+ {report['train_windows_synthetic']} synthetic normal for `synthetic+real`).
- Test windows: `{json.dumps(report['test_windows'])}`.
- Windows overlap (stride 1), so neighbouring test windows are correlated;
  treat these as estimates from one ~1-hour run, not a benchmark.

## Overall (same real test set for every model)

{header}
{sep}
{chr(10).join(rows)}

`real` confusion matrix: TN={c['true_negatives']}, FP={c['false_positives']},
FN={c['false_negatives']}, TP={c['true_positives']}.

## Per scenario

Precision / recall / F1 for `real` are computed on that scenario's windows plus
all normal test windows. The other columns are recall only.

{scen_header}
{scen_sep}
{chr(10).join(scen_rows)}

## False-positive rate by namespace (normal test windows)

| Namespace | {' | '.join(f'`{n}`' for n in names)} |
|---{'|---' * len(names)}|
{fp_rows}

## Threshold sensitivity (`real` model)

Reported for transparency only; the headline numbers above use the
pre-declared {report['threshold_percentile']:.0f}th percentile.

| Percentile | Threshold | Precision | Recall | F1 | Specificity |
|---|---|---|---|---|---|
{sweep_rows}
"""


def _log_mlflow(report: dict, run_meta: dict, report_json: Path, model_path: Path, tracking_uri: str) -> list[str]:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("kube-copilot-anomaly-real")
    run_ids = []
    for name, result in report["models"].items():
        with mlflow.start_run(run_name=f"{name}-{run_meta.get('run_id')}") as run:
            mlflow.log_params(
                {
                    "training_data": name,
                    "data_run_id": run_meta.get("run_id"),
                    "data_minutes": run_meta.get("minutes"),
                    "threshold_percentile": report["threshold_percentile"],
                    "n_estimators": report["n_estimators"],
                    "seed": report["seed"],
                    "window_size": report["window_size"],
                    "train_windows_real": report["train_windows"] if name != "synthetic" else 0,
                }
            )
            flat = {k: result[k] for k in ("precision", "recall", "f1", "accuracy", "specificity", "threshold")}
            for scenario, values in result["per_scenario"].items():
                flat[f"recall_{scenario}"] = values["recall"]
            mlflow.log_metrics({k: float(v) for k, v in flat.items() if v is not None})
            mlflow.log_artifact(str(report_json), artifact_path="reports")
            if name == "real" and model_path.is_file():
                mlflow.log_artifact(str(model_path), artifact_path="model")
            run_ids.append(run.info.run_id)
    return run_ids


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train/evaluate on real collected metrics.")
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--window-size", type=int, default=12)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--threshold-percentile", type=float, default=90.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--no-synthetic", action="store_true", help="skip the synthetic comparisons")
    parser.add_argument("--no-mlflow", action="store_true")
    parser.add_argument(
        "--mlflow-uri", default=os.environ.get("MLFLOW_TRACKING_URI", f"file://{DEFAULT_MLRUNS}")
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = args.run_dir or latest_run_dir()
    points, timeline = load_run(run_dir)
    run_meta = {"run_id": timeline.run_id}
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        run_meta.update(json.loads(summary_path.read_text(encoding="utf-8")))

    windows = build_windows(points, window_size=args.window_size, stride=args.stride, step_seconds=timeline.step_seconds)
    artifact, report = train_and_evaluate(
        windows,
        baseline_end(timeline),
        seed=args.seed,
        n_estimators=args.n_estimators,
        threshold_percentile=args.threshold_percentile,
        include_synthetic=not args.no_synthetic,
    )
    report["data"] = {k: run_meta.get(k) for k in ("run_id", "minutes", "rows", "pods", "start_utc", "end_utc")}
    artifact["training_data"] = f"real:{timeline.run_id}"
    save_artifact(artifact, args.output)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.report_md.write_text(render_markdown(report, run_meta), encoding="utf-8")

    if not args.no_mlflow:
        try:
            run_ids = _log_mlflow(report, run_meta, args.report_json, args.output, args.mlflow_uri)
            print(f"MLflow runs: {', '.join(run_ids)}  (ui: make mlflow-ui)")
        except Exception as exc:  # noqa: BLE001 — reports are still written
            print(f"MLflow logging failed (reports were still written): {exc}", file=sys.stderr)

    for name, result in report["models"].items():
        print(f"{name:15s} precision={_fmt(result['precision'])} recall={_fmt(result['recall'])} f1={_fmt(result['f1'])}")
    print(f"Wrote {args.output}, {args.report_json}, {args.report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
