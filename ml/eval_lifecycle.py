"""Evaluate the event/lifecycle detector (ml/lifecycle.py) next to the Isolation Forest.

Usage (from the repo root, with the backend virtualenv):

    PYTHONPATH=. python -m ml.eval_lifecycle --tune-run ml/data/real/run-box-03 \
        --heldout-run ml/data/real/run-box-04
    make eval-lifecycle

Units are the same as ml/train_real.py: 3-minute windows of one pod (12 x 15s),
labelled from the collector's per-point labels, ambiguous windows dropped.

- The Isolation Forest is `synthetic+real+guard` exactly as in ml/train_real.py:
  trained on synthetic normal windows plus every normal window of the
  training run (`run-box-01`), thresholded at the 90th percentile of its own
  training scores. It never sees the runs scored here.
- The lifecycle detector has no learned parameters. A window is flagged when
  any signal on the pod's workload falls inside it (see `SignalIndex`).
- `union` flags a window when either detector does.

The rules in ml/lifecycle.py were written and frozen before the held-out run
(`run-box-03`) was collected; the only data looked at while writing them was
a 4.5-minute pod_kill smoke run that is not in the repo. `--tune-run` scores
an extra run under a separate heading if one is ever added.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ml.collect import DEFAULT_OUTPUT_ROOT, LABEL_NORMAL, Timeline, read_events
from ml.features import extract_feature_matrix
from ml.lifecycle import (
    DetectorConfig,
    LifecycleEvent,
    PodLife,
    RestartObservation,
    Signal,
    SignalIndex,
    WorkloadSample,
    detect,
    workload_of,
)
from ml.real_data import DROPPED_LABELS, RealPoint, RealWindow, build_windows, load_run
from ml.train import REPO_ROOT, fit_isolation_forest, predict
from ml.train import train as train_synthetic
from ml.train_real import _fmt, apply_scope, evaluate_predictions

DEFAULT_REPORT_JSON = REPO_ROOT / "ml" / "reports" / "lifecycle_metrics.json"
DEFAULT_REPORT_MD = REPO_ROOT / "ml" / "reports" / "lifecycle_metrics.md"


# ---------------------------------------------------------------- loading


def read_lifecycle(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Runs collected before lifecycle export (run-box-01/02) "
            "have no events or kube-state-metrics lifecycle series."
        )
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return [
            {**row, "ts": float(row["ts"]), "value": float(row["value"])}
            for row in csv.DictReader(handle)
        ]


def to_events(raw: list[dict]) -> list[LifecycleEvent]:
    return [
        LifecycleEvent(
            ts=float(e["last_ts"]),
            first_ts=float(e["first_ts"]),
            namespace=e["namespace"],
            kind=e["kind"],
            name=e["name"],
            reason=e["reason"],
            type=e.get("type", ""),
            message=e.get("message", ""),
            count=int(e.get("count", 1)),
        )
        for e in raw
    ]


def to_pods(rows: list[dict]) -> list[PodLife]:
    owners: dict[tuple[str, str], str] = {}
    created: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        key = (row["namespace"], row["name"])
        if row["series"] == "pod_owner" and row["extra"]:
            owners[key] = row["extra"]
        elif row["series"] == "pod_created":
            created[key].append((row["ts"], row["value"]))
    pods = []
    for (ns, pod), samples in created.items():
        samples.sort()
        pods.append(
            PodLife(
                namespace=ns,
                pod=pod,
                workload=workload_of(pod, owners.get((ns, pod))),
                created_ts=samples[-1][1],
                first_seen=samples[0][0],
                last_seen=samples[-1][0],
            )
        )
    return pods


def to_workloads(rows: list[dict]) -> list[WorkloadSample]:
    grid: dict[tuple[str, str, float], dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row["series"].startswith("deploy_"):
            grid[(row["namespace"], row["name"], row["ts"])][row["series"]] = row["value"]
    out = []
    for (ns, name, ts), values in sorted(grid.items(), key=lambda kv: kv[0][2]):
        if "deploy_desired" not in values or "deploy_available" not in values:
            continue
        out.append(
            WorkloadSample(
                ts=ts,
                namespace=ns,
                workload=name,
                desired=values["deploy_desired"],
                available=values["deploy_available"],
                generation=values.get("deploy_generation", 0.0),
            )
        )
    return out


def to_restarts(points: list[RealPoint], rows: list[dict], pod_workloads: dict[tuple[str, str], str]) -> list[RestartObservation]:
    reasons: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for row in rows:
        if row["series"] == "pod_last_terminated" and row["value"] >= 1:
            reasons[(row["namespace"], row["name"])].append((row["ts"], row["extra"]))
    by_pod: dict[tuple[str, str], list[RealPoint]] = defaultdict(list)
    for point in points:
        by_pod[(point.namespace, point.pod)].append(point)
    out = []
    for key, series in by_pod.items():
        series.sort(key=lambda p: p.ts)
        for prev, cur in zip(series, series[1:]):
            if cur.restarts > prev.restarts:
                later = [r for t, r in reasons.get(key, []) if t >= cur.ts]
                out.append(
                    RestartObservation(
                        ts=cur.ts,
                        namespace=key[0],
                        pod=key[1],
                        workload=pod_workloads.get(key) or workload_of(key[1]),
                        delta=cur.restarts - prev.restarts,
                        reason=later[0] if later else "",
                    )
                )
    return out


def run_signals(run_dir: Path, points: list[RealPoint], timeline: Timeline) -> tuple[list[Signal], dict]:
    rows = read_lifecycle(run_dir / "lifecycle.csv.gz")
    events = to_events(read_events(run_dir / "events.jsonl.gz"))
    pods = to_pods(rows)
    pod_workloads = {(p.namespace, p.pod): p.workload for p in pods}
    config = DetectorConfig(observation_start=timeline.start, observation_end=timeline.end)
    signals = detect(
        events=events,
        pods=pods,
        workloads=to_workloads(rows),
        restarts=to_restarts(points, rows, pod_workloads),
        config=config,
    )
    signals = [s for s in signals if timeline.start - 60 <= s.ts <= timeline.end]
    return signals, pod_workloads


# ---------------------------------------------------------------- evaluation


def lifecycle_predictions(windows: list[RealWindow], index: SignalIndex) -> tuple[np.ndarray, list[list[Signal]]]:
    hits = [index.for_window(w.namespace, w.pod, w.start, w.end) for w in windows]
    return np.array([1 if h else 0 for h in hits], dtype=np.int32), hits


def phase_latency(timeline: Timeline, signals: list[Signal], index: SignalIndex) -> list[dict]:
    """Seconds from each chaos window's start to the first lifecycle signal on its target."""
    out = []
    for phase in timeline.phases:
        if phase.kind != "chaos" or not phase.target_prefix:
            continue
        target = phase.target_prefix.rstrip("-")  # e.g. victim-kill- -> victim-kill
        on_target = [
            s for s in signals
            if s.namespace == timeline.namespace and s.workload == target and phase.start <= s.ts < phase.end
        ]
        first = min((s.ts for s in on_target), default=None)
        out.append(
            {
                "scenario": phase.scenario,
                "start_utc": datetime.fromtimestamp(phase.start, timezone.utc).strftime("%H:%M:%SZ"),
                "minutes": round((phase.end - phase.start) / 60, 1),
                "signals": len(on_target),
                "kinds": dict(Counter(s.kind for s in on_target)),
                "first_signal_after_s": None if first is None else round(first - phase.start, 1),
            }
        )
    return out


def fit_forest(train_windows: list[RealWindow], seed: int = 42, n_estimators: int = 200, pct: float = 90.0) -> dict:
    """`synthetic+real+guard` from ml/train_real.py, refit deterministically."""
    train = [w for w in train_windows if w.label == LABEL_NORMAL]
    x_train = extract_feature_matrix([w.points for w in train])
    _, _, syn_train, _ = train_synthetic(seed=seed, n_estimators=n_estimators, threshold_percentile=pct)
    x_syn = extract_feature_matrix([s.points for s in syn_train])
    return fit_isolation_forest(
        np.vstack([x_syn, x_train]),
        range_guard=True,
        seed=seed,
        n_estimators=n_estimators,
        threshold_percentile=pct,
        window_size=len(train[0].points),
    )


def evaluate_run(run_dir: Path, forest: dict, scope: str) -> dict:
    points, timeline = load_run(run_dir)
    windows = build_windows(points, step_seconds=timeline.step_seconds)
    test = [w for w in apply_scope(windows, scope) if w.label not in DROPPED_LABELS]
    signals, pod_workloads = run_signals(run_dir, points, timeline)
    index = SignalIndex(signals, pod_workloads)

    if_pred = predict(forest, extract_feature_matrix([w.points for w in test]))
    lc_pred, hits = lifecycle_predictions(test, index)
    union = np.maximum(if_pred, lc_pred)
    models = {
        "isolation_forest": evaluate_predictions(test, if_pred, float(forest["threshold"])),
        "lifecycle": evaluate_predictions(test, lc_pred),
        "union": evaluate_predictions(test, union),
    }
    fp_kinds = Counter(
        s.kind for w, h in zip(test, hits, strict=True) if not w.is_anomaly for s in h[:1]
    )
    fp_examples = [
        {"pod": f"{w.namespace}/{w.pod}", "signal": h[0].kind, "detail": h[0].detail,
         "at_utc": datetime.fromtimestamp(h[0].ts, timezone.utc).strftime("%H:%M:%SZ")}
        for w, h in zip(test, hits, strict=True) if h and not w.is_anomaly
    ]
    seen = set()
    fp_unique = []
    for ex in fp_examples:
        key = (ex["pod"].rsplit("-", 2)[0], ex["signal"], ex["at_utc"])
        if key not in seen:
            seen.add(key)
            fp_unique.append(ex)
    app_signals = [s for s in signals if scope == "all" or s.namespace == timeline.namespace]
    return {
        "run_id": timeline.run_id,
        "scope": scope,
        "test_windows": dict(sorted(Counter(w.label for w in test).items())),
        "models": models,
        "signals_by_kind": dict(sorted(Counter(s.kind for s in app_signals).items())),
        "false_positive_first_signal_kinds": dict(fp_kinds),
        "false_positive_examples": fp_unique[:15],
        "phase_latency": phase_latency(timeline, signals, index),
    }


# ---------------------------------------------------------------- report


def render_section(title: str, result: dict) -> str:
    models = result["models"]
    names = list(models)
    head = "| Metric | " + " | ".join(f"`{n}`" for n in names) + " |\n|---" + "|---" * len(names) + "|"
    rows = "\n".join(
        f"| {k.capitalize()} | " + " | ".join(_fmt(models[n][k]) for n in names) + " |"
        for k in ("precision", "recall", "f1", "specificity")
    )
    confusion = "\n".join(
        f"- `{n}`: TN={m['confusion']['true_negatives']}, FP={m['confusion']['false_positives']}, "
        f"FN={m['confusion']['false_negatives']}, TP={m['confusion']['true_positives']}"
        for n, m in models.items()
    )
    scenarios = sorted({s for m in models.values() for s in m["per_scenario"]})
    scen = "\n".join(
        f"| `{s}` | {models[names[0]]['per_scenario'][s]['windows']} | "
        + " | ".join(_fmt(models[n]["per_scenario"][s]["recall"]) for n in names)
        + " |"
        for s in scenarios
    )
    lat = "\n".join(
        f"| `{p['scenario']}` | {p['start_utc']} | {p['minutes']} | {p['signals']} | "
        f"{_fmt_s(p['first_signal_after_s'])} | {', '.join(f'{k} x{v}' for k, v in sorted(p['kinds'].items())) or '-'} |"
        for p in result["phase_latency"]
    )
    fps = "\n".join(
        f"- `{e['pod']}` at {e['at_utc']}: `{e['signal']}` {e['detail']}" for e in result["false_positive_examples"]
    ) or "- none"
    return f"""## {title}

Test windows: `{json.dumps(result['test_windows'])}`.

{head}
{rows}

{confusion}

### Recall per scenario

| Scenario | Windows | {' | '.join(f'`{n}`' for n in names)} |
|---|---{'|---' * len(names)}|
{scen}

### Time to first lifecycle signal per chaos window

| Scenario | Start (UTC) | Minutes | Signals | First signal after | Signal kinds |
|---|---|---|---|---|---|
{lat}

### Lifecycle false positives (first signal per normal window, de-duplicated)

{fps}
"""


def _fmt_s(value: float | None) -> str:
    return "never" if value is None else f"{value:.0f}s"


def render_markdown(sections: list[tuple[str, dict]], meta: dict) -> str:
    body = "\n".join(render_section(t, r) for t, r in sections)
    return f"""# Pod disruption detection from events and lifecycle state

Generated by `make eval-lifecycle` (`ml/eval_lifecycle.py`).

- Isolation Forest: `synthetic+real+guard`, trained on `{meta['train_run']}` only (as in `real_metrics.md`).
- Lifecycle detector: rules in `ml/lifecycle.py`, no learned parameters.
- Held-out run: `{meta['heldout_run']}`, collected after the rules were frozen. Nothing was
  tuned on it; the only data seen while writing the rules was a 4.5-minute pod_kill smoke run.
- Extra tuning run: `{meta.get('tune_run') or 'none'}`.
- Units: 3-minute windows of one pod (12 x 15s), ambiguous windows dropped,
  stride 1 so neighbouring windows are correlated.
- `union` = flagged by either detector.

{body}"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train-run", type=Path, default=DEFAULT_OUTPUT_ROOT / "run-box-01")
    parser.add_argument("--heldout-run", type=Path, default=DEFAULT_OUTPUT_ROOT / "run-box-03")
    parser.add_argument("--tune-run", type=Path, default=None)
    parser.add_argument("--scopes", default="apps,all")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    train_points, train_tl = load_run(args.train_run)
    train_windows = build_windows(train_points, step_seconds=train_tl.step_seconds)
    scope_names = {"apps": "app namespaces only", "all": "all namespaces"}
    sections: list[tuple[str, dict]] = []
    payload: dict = {}
    for scope in args.scopes.split(","):
        forest = fit_forest(apply_scope(train_windows, scope))
        runs = [("heldout", args.heldout_run)] + ([("tune", args.tune_run)] if args.tune_run else [])
        for role, run_dir in runs:
            result = evaluate_run(run_dir, forest, scope)
            payload[f"{role}_{scope}"] = result
            label = "Held-out" if role == "heldout" else "Tuning"
            sections.append((f"{label} run `{result['run_id']}`, {scope_names[scope]}", result))
    # Held-out app namespaces first: it is the number to quote.
    order = {"heldout_apps": 0, "tune_apps": 1, "heldout_all": 2, "tune_all": 3}
    keys = sorted(payload, key=lambda k: order.get(k, 9))
    sections = [next(s for s in sections if s[1] is payload[k]) for k in keys]
    meta = {
        "train_run": args.train_run.name,
        "tune_run": args.tune_run.name if args.tune_run else None,
        "heldout_run": args.heldout_run.name,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    payload["meta"] = meta
    args.report_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.report_md.write_text(render_markdown(sections, meta), encoding="utf-8")
    for title, result in sections:
        print(title)
        for name, m in result["models"].items():
            pk = m["per_scenario"].get("pod_kill", {}).get("recall")
            print(f"  {name:18s} P={_fmt(m['precision'])} R={_fmt(m['recall'])} F1={_fmt(m['f1'])} pod_kill_R={_fmt(pk)}")
    print(f"Wrote {args.report_json}, {args.report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
