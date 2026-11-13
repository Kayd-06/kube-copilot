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
from ml.real_data import (
    DROPPED_LABELS,
    RealWindow,
    baseline_end,
    build_windows,
    load_run,
    time_split,
    window_counts,
)
from ml.train import REPO_ROOT, fit_isolation_forest, metrics, predict, raw_scores, save_artifact
from ml.train import train as train_synthetic

DEFAULT_MODEL_PATH = REPO_ROOT / "backend" / "models" / "isolation_forest_real.joblib"
DEFAULT_REPORT_JSON = REPO_ROOT / "ml" / "reports" / "real_metrics.json"
DEFAULT_REPORT_MD = REPO_ROOT / "ml" / "reports" / "real_metrics.md"
DEFAULT_MLRUNS = REPO_ROOT / "mlruns"
SWEEP_PERCENTILES = (90.0, 95.0, 99.0)
# Cluster plumbing, not user workloads. The chaos tooling itself lives in
# chaos-mesh and its activity is caused by the experiments, Prometheus grows
# its TSDB all run, and control-plane pods have bursty, unlimited usage.
INFRA_NAMESPACES = frozenset({"chaos-mesh", "kube-system", "observability", "local-path-storage"})
SCOPES = ("all", "apps")


def apply_scope(windows: list[RealWindow], scope: str) -> list[RealWindow]:
    """`all` keeps every pod; `apps` drops pods in INFRA_NAMESPACES (train and test)."""
    if scope == "all":
        return windows
    if scope == "apps":
        return [w for w in windows if w.namespace not in INFRA_NAMESPACES]
    raise ValueError(f"unknown scope {scope!r}")


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
    y_pred = predict(artifact, x, threshold)
    return evaluate_predictions(test, y_pred, float(artifact["threshold"] if threshold is None else threshold))


def evaluate_predictions(test: list[RealWindow], y_pred: np.ndarray, threshold: float | None = None) -> dict:
    """The same report as `evaluate`, for predictions from any detector."""
    y_pred = np.asarray(y_pred, dtype=np.int32)
    y_true = np.array([1 if w.is_anomaly else 0 for w in test], dtype=np.int32)
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
        "threshold": threshold,
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
    heldout: list[RealWindow] | None = None,
) -> tuple[dict, dict]:
    """Return (artifact for `real+guard`, report).

    Without `heldout`: time split inside one run at `split_ts`.
    With `heldout` (windows from a later run): train on every normal window of
    `windows` and test on all of `heldout`.
    """
    if heldout is None:
        train, test = time_split(windows, split_ts)
    else:
        train = [w for w in windows if w.label == LABEL_NORMAL]
        test = [w for w in heldout if w.label not in DROPPED_LABELS]
    if not train:
        raise RuntimeError("No normal training windows before the split; collect a longer baseline.")
    if not any(w.is_anomaly for w in test):
        raise RuntimeError("No chaos windows in the test set; check the timeline and labels.")

    x_train = extract_feature_matrix([w.points for w in train])
    window_size = len(train[0].points)
    common = {
        "seed": seed,
        "n_estimators": n_estimators,
        "threshold_percentile": threshold_percentile,
        "window_size": window_size,
    }
    real_if = fit_isolation_forest(x_train, **common)
    real_guard = fit_isolation_forest(x_train, range_guard=True, **common)
    models: dict[str, dict] = {"real": evaluate(real_if, test), "real+guard": evaluate(real_guard, test)}

    sweep = {}
    train_scores = raw_scores(real_if, x_train)
    for pct in SWEEP_PERCENTILES:
        cut = float(np.percentile(train_scores, pct))
        result = evaluate(real_if, test, threshold=cut)
        sweep[f"p{pct:.0f}"] = {k: result[k] for k in ("threshold", "precision", "recall", "f1", "specificity")}

    n_syn = 0
    if include_synthetic:
        syn_artifact, _, syn_train, _ = train_synthetic(
            seed=seed, n_estimators=n_estimators, threshold_percentile=threshold_percentile
        )
        models["synthetic"] = evaluate(syn_artifact, test)
        x_syn = extract_feature_matrix([s.points for s in syn_train])
        syn_guard = fit_isolation_forest(x_syn, range_guard=True, **common)
        models["synthetic+guard"] = evaluate(syn_guard, test)
        combined = fit_isolation_forest(np.vstack([x_syn, x_train]), range_guard=True, **common)
        models["synthetic+real+guard"] = evaluate(combined, test)
        n_syn = len(syn_train)

    split_desc = (
        f"time split inside one run at {datetime.fromtimestamp(split_ts, timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
        if heldout is None
        else "train on every normal window of the training run, test on a later held-out run"
    )
    report = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **common,
        "split": split_desc,
        "train_windows": len(train),
        "train_windows_synthetic": n_syn,
        "test_windows": window_counts(test),
        "models": models,
        "threshold_sweep_real": sweep,
    }
    return real_guard, report


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_section(report: dict, title: str) -> str:
    models = report["models"]
    names = list(models)
    header = "| Metric | " + " | ".join(f"`{n}`" for n in names) + " |"
    sep = "|---" * (len(names) + 1) + "|"
    rows = [
        "| " + key.capitalize() + " | " + " | ".join(_fmt(models[n][key]) for n in names) + " |"
        for key in ("precision", "recall", "f1", "specificity")
    ]
    scenarios = sorted({s for m in models.values() for s in m["per_scenario"]})
    scen_header = "| Scenario | Windows | " + " | ".join(f"`{n}` P / R / F1" for n in names) + " |"
    scen_sep = "|---" * (len(names) + 2) + "|"
    scen_rows = []
    for scenario in scenarios:
        n_windows = models[names[0]]["per_scenario"].get(scenario, {}).get("windows", 0)
        cells = [f"`{scenario}`", str(n_windows)]
        for name in names:
            v = models[name]["per_scenario"].get(scenario, {})
            cells.append(f"{_fmt(v.get('precision'))} / {_fmt(v.get('recall'))} / {_fmt(v.get('f1'))}")
        scen_rows.append("| " + " | ".join(cells) + " |")
    namespaces = sorted({ns for m in models.values() for ns in m["false_positive_rate_by_namespace"]})
    fp_rows = "\n".join(
        f"| `{ns}` | " + " | ".join(_fmt(models[n]["false_positive_rate_by_namespace"].get(ns)) for n in names) + " |"
        for ns in namespaces
    )
    sweep_rows = "\n".join(
        f"| {k} | {v['threshold']:.4f} | {_fmt(v['precision'])} | {_fmt(v['recall'])} | {_fmt(v['f1'])} | {_fmt(v['specificity'])} |"
        for k, v in report["threshold_sweep_real"].items()
    )
    confusion = "\n".join(
        f"- `{n}`: TN={m['confusion']['true_negatives']}, FP={m['confusion']['false_positives']}, "
        f"FN={m['confusion']['false_negatives']}, TP={m['confusion']['true_positives']}"
        for n, m in models.items()
    )
    return f"""## {title}

- Split: {report['split']}.
- Train windows: {report['train_windows']} real normal
  (+ {report['train_windows_synthetic']} synthetic normal for the `synthetic*` models).
- Test windows: `{json.dumps(report['test_windows'])}`.

### Overall

{header}
{sep}
{chr(10).join(rows)}

{confusion}

### Per scenario

Each scenario's precision / recall / F1 is computed on that scenario's windows
plus every normal test window.

{scen_header}
{scen_sep}
{chr(10).join(scen_rows)}

### False-positive rate by namespace (normal test windows)

| Namespace | {' | '.join(f'`{n}`' for n in names)} |
|---{'|---' * len(names)}|
{fp_rows}

### Threshold sensitivity (`real`, Isolation Forest only)

For transparency only; the tables above use the pre-declared
{report['threshold_percentile']:.0f}th percentile of training scores.

| Percentile | Threshold | Precision | Recall | F1 | Specificity |
|---|---|---|---|---|---|
{sweep_rows}
"""


def _run_line(meta: dict) -> str:
    return (
        f"`{meta.get('run_id')}`: {meta.get('minutes')} min wall clock, {meta.get('rows')} pod samples, "
        f"{meta.get('pods')} pods ({meta.get('start_utc', '')[:16]}Z to {meta.get('end_utc', '')[:16]}Z)"
    )


def render_markdown(sections: list[tuple[str, dict]], runs: list[dict]) -> str:
    run_lines = "\n".join(f"- {_run_line(m)}" for m in runs)
    first = sections[0][1]
    body = "\n".join(render_section(report, title) for title, report in sections)
    return f"""# Isolation Forest on real cluster metrics

Data: Prometheus metrics from a single-node kind cluster while Chaos Mesh
experiments ran on a schedule (see `ml/data/real/README.md`).

{run_lines}

## Method

- Window = {first['window_size']} samples x 15s = 3 minutes of one pod, the same features as the live API.
- Every model is trained on **normal windows only** and thresholded at the
  {first['threshold_percentile']:.0f}th percentile of its own training scores, so test data never sets a threshold.
- No leakage: training windows always end before any test window starts.
  Ambiguous windows (chaos edges, a minute of recovery after chaos) are dropped.
- Windows overlap (stride 1), so neighbouring test windows are correlated.
  These are estimates from about two hours of one laptop-sized cluster, not a benchmark.

Models:

- `real`: Isolation Forest on real normal windows (the method defined before any real data existed).
- `real+guard`: the same forest plus an out-of-range guard. It flags a window when
  any model feature lies more than one training-range width outside the training
  [p0.5, p99.5] band, because Isolation Forest cannot score points far beyond its
  training range above the most extreme training point. **The guard was designed
  after looking at the in-run results below**, so its in-run numbers are optimistic.
  The held-out run was collected and scored only after the code was frozen; treat
  that section as the honest estimate.
- `synthetic`: the existing synthetic-trained model (`make train` defaults).
- `synthetic+guard`, `synthetic+real+guard`: synthetic-only, and synthetic plus real normal windows, with the guard.

Scopes (applied to training and test windows alike):

- **all namespaces**: every pod in the cluster.
- **app namespaces only**: drops `chaos-mesh`, `kube-system`, `observability` and
  `local-path-storage`. Those are cluster plumbing rather than workloads a user
  asks the copilot about, and their behaviour is driven by the experiments
  themselves (the chaos daemon works hardest while injecting) or grows all run
  (Prometheus' TSDB). This scope was also chosen after the first in-run results;
  it was fixed before the held-out run was scored.

{body}"""


def _log_mlflow(
    report: dict, evaluation: str, runs: list[dict], report_json: Path, model_path: Path, tracking_uri: str
) -> list[str]:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("kube-copilot-anomaly-real")
    data_runs = ",".join(str(m.get("run_id")) for m in runs)
    run_ids = []
    for name, result in report["models"].items():
        with mlflow.start_run(run_name=f"{evaluation}-{name}") as run:
            mlflow.log_params(
                {
                    "model_variant": name,
                    "evaluation": evaluation,
                    "split": report["split"],
                    "data_runs": data_runs,
                    "threshold_percentile": report["threshold_percentile"],
                    "n_estimators": report["n_estimators"],
                    "seed": report["seed"],
                    "window_size": report["window_size"],
                    "train_windows_real": report["train_windows"] if not name.startswith("synthetic+guard") and name != "synthetic" else 0,
                }
            )
            flat = {k: result[k] for k in ("precision", "recall", "f1", "accuracy", "specificity", "threshold")}
            for scenario, values in result["per_scenario"].items():
                flat[f"recall_{scenario}"] = values["recall"]
                flat[f"f1_{scenario}"] = values["f1"]
            mlflow.log_metrics({k: float(v) for k, v in flat.items() if v is not None})
            mlflow.log_artifact(str(report_json), artifact_path="reports")
            if name == "real+guard" and model_path.is_file():
                mlflow.log_artifact(str(model_path), artifact_path="model")
            run_ids.append(run.info.run_id)
    return run_ids


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train/evaluate on real collected metrics.")
    parser.add_argument("--run-dir", type=Path, default=None, help="training run (default: oldest collected run)")
    parser.add_argument(
        "--heldout-run-dir", type=Path, default=None,
        help="later run used only for testing (default: the next collected run, if any)",
    )
    parser.add_argument("--no-heldout", action="store_true")
    parser.add_argument("--scopes", default=",".join(SCOPES), help="comma-separated: all, apps")
    parser.add_argument(
        "--ship-scope", default="apps", choices=SCOPES,
        help="which scope's real+guard model is written to --output",
    )
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


def collected_runs(root: Path = DEFAULT_OUTPUT_ROOT) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/points.csv.gz"))


def _load(run_dir: Path, args: argparse.Namespace) -> tuple[list[RealWindow], float, dict]:
    points, timeline = load_run(run_dir)
    meta = {"run_id": timeline.run_id}
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        meta.update(json.loads(summary_path.read_text(encoding="utf-8")))
    windows = build_windows(points, window_size=args.window_size, stride=args.stride, step_seconds=timeline.step_seconds)
    return windows, baseline_end(timeline), meta


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runs = collected_runs()
    run_dir = args.run_dir or (runs[0] if runs else latest_run_dir())
    heldout_dir = args.heldout_run_dir
    if heldout_dir is None and not args.no_heldout and args.run_dir is None:
        later = [r for r in runs if r != run_dir]
        heldout_dir = later[0] if later else None

    windows, split_ts, meta = _load(run_dir, args)
    h_windows, h_meta = None, None
    if heldout_dir is not None:
        h_windows, _, h_meta = _load(heldout_dir, args)
    kwargs = {
        "seed": args.seed,
        "n_estimators": args.n_estimators,
        "threshold_percentile": args.threshold_percentile,
        "include_synthetic": not args.no_synthetic,
    }
    sections: list[tuple[str, str, dict]] = []
    payload: dict = {}
    artifact: dict = {}
    scope_names = {"all": "all namespaces", "apps": "app namespaces only"}
    for scope in args.scopes.split(","):
        art, in_run = train_and_evaluate(apply_scope(windows, scope), split_ts, **kwargs)
        in_run["scope"] = scope
        key = f"in_run_{scope}"
        payload[key] = in_run
        sections.append((key, f"In-run time split (`{meta.get('run_id')}`), {scope_names[scope]}", in_run))
        if scope == args.ship_scope:
            artifact = art
        if h_windows is not None:
            art, heldout = train_and_evaluate(
                apply_scope(windows, scope), split_ts, heldout=apply_scope(h_windows, scope), **kwargs
            )
            heldout["scope"] = scope
            key = f"heldout_{scope}"
            payload[key] = heldout
            sections.append((
                key,
                f"Held-out run (train `{meta.get('run_id')}`, test `{h_meta.get('run_id')}`), {scope_names[scope]}",
                heldout,
            ))
            if scope == args.ship_scope:
                artifact = art
    run_metas = [meta] + ([h_meta] if h_meta else [])
    payload["runs"] = run_metas

    artifact["training_data"] = f"real:{meta.get('run_id')}"
    save_artifact(artifact, args.output)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.report_md.write_text(render_markdown([(t, r) for _, t, r in sections], run_metas), encoding="utf-8")

    if not args.no_mlflow:
        try:
            run_ids = []
            for key, _, report in sections:
                run_ids += _log_mlflow(report, key, run_metas, args.report_json, args.output, args.mlflow_uri)
            print(f"MLflow runs: {', '.join(run_ids)}  (ui: make mlflow-ui)")
        except Exception as exc:  # noqa: BLE001 — reports are still written
            print(f"MLflow logging failed (reports were still written): {exc}", file=sys.stderr)

    for _, title, report in sections:
        print(title)
        for name, result in report["models"].items():
            print(f"  {name:22s} precision={_fmt(result['precision'])} recall={_fmt(result['recall'])} f1={_fmt(result['f1'])}")
    print(f"Wrote {args.output}, {args.report_json}, {args.report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
