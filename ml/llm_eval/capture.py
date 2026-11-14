"""Reproduce the fault scenarios on the kind cluster and freeze what Diagnose and the proposer see.

Usage (cluster from `make cluster-up metrics-up chaos-up`, Prometheus on :9090):

    PYTHONPATH=.:backend python -m ml.llm_eval.capture                # every case
    PYTHONPATH=.:backend python -m ml.llm_eval.capture --only oom_first,healthy_web --keep
    PYTHONPATH=.:backend python -m ml.llm_eval.capture --set heldout      # the held-out cases

Each case gets its own namespace `kc-eval-NN` (label kube-copilot.io/eval=true),
which is deleted afterwards unless --keep. The context is collected with the
API's own code paths (K8sClient, retriever, anomaly scorer + lifecycle
detector, ActionsCluster), so the fixture is exactly what Diagnose and the
safe-actions proposer receive. Labels come from cases.py, never from a model.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QDRANT_URL", ":memory:")

from ml.llm_eval.cases import CASES, Case  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "llm_eval"
# Schema 2 adds diagnose_input.usage, containers[].resources and the
# Deployment's template_diff / template_refs.
SCHEMA_VERSION = 2
EVAL_LABEL = "kube-copilot.io/eval"
log = logging.getLogger("capture")
_print_lock = threading.Lock()


def say(case_id: str, msg: str) -> None:
    with _print_lock:
        print(f"{datetime.now().strftime('%H:%M:%S')} [{case_id}] {msg}", flush=True)


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def apply(ns: str, objs: list[dict]) -> None:
    for obj in objs:
        obj = json.loads(json.dumps(obj))
        obj.setdefault("metadata", {})["namespace"] = ns
        kubectl("apply", "-f", "-", stdin=json.dumps(obj))


def pods(ns: str, app: str) -> list[dict]:
    items = json.loads(kubectl("-n", ns, "get", "pods", "-l", f"app={app}", "-o", "json"))["items"]
    items = [p for p in items if not p["metadata"].get("deletionTimestamp")]
    return sorted(items, key=lambda p: (p["metadata"]["creationTimestamp"], p["metadata"]["name"]), reverse=True)


def wait_for(fn, timeout: float, interval: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(interval)
    return None


def find_target(ns: str, case: Case) -> dict | None:
    for pod in pods(ns, case.workload):
        if case.expect(pod):
            return pod
    return None


def pod_kill(ns: str, case: Case, times: int) -> None:
    # Run steady first: the lifecycle detector treats churn within 120s of a scale/rollout as expected.
    time.sleep(160)
    for n in range(times):
        name = f"kill-{n}"
        kubectl("apply", "-f", "-", stdin=json.dumps({
            "apiVersion": "chaos-mesh.org/v1alpha1", "kind": "PodChaos",
            "metadata": {"name": name, "namespace": ns},
            "spec": {"action": "pod-kill", "mode": "one",
                     "selector": {"namespaces": [ns], "labelSelectors": {"app": case.workload}}},
        }))
        say(case.id, f"pod-kill {n + 1}/{times}")
        time.sleep(30 if n < times - 1 else 5)
    kubectl("-n", ns, "rollout", "status", f"deploy/{case.workload}", "--timeout=120s")


def stale_dependency(ns: str, case: Case) -> None:
    """web starts while deps-api has 0 replicas, then deps-api comes back."""
    wait_for(lambda: any(p.get("status", {}).get("phase") == "Running" for p in pods(ns, case.workload)), 120)
    time.sleep(10)
    kubectl("-n", ns, "scale", "deploy/deps-api", "--replicas=1")
    kubectl("-n", ns, "rollout", "status", "deploy/deps-api", "--timeout=120s")


def setup_case(ns: str, case: Case) -> None:
    kubectl("create", "namespace", ns, check=False)
    kubectl("label", "namespace", ns, f"{EVAL_LABEL}=true", "--overwrite")
    apply(ns, case.base)
    if case.rollout or (case.hook or "").startswith("pod_kill"):
        kubectl("-n", ns, "rollout", "status", f"deploy/{case.workload}", "--timeout=180s")
        time.sleep(5)
    if case.rollout:
        apply(ns, case.rollout)
        if case.post or case.hook:
            kubectl("-n", ns, "rollout", "status", f"deploy/{case.workload}", "--timeout=180s")
            time.sleep(5)
    for args in case.post:
        kubectl("-n", ns, *args)
    if case.hook == "stale_dependency":
        stale_dependency(ns, case)
    elif case.hook and case.hook.startswith("pod_kill:"):
        pod_kill(ns, case, int(case.hook.split(":")[1]))


def capture_context(ns: str, pod_name: str, workload: str) -> dict:
    """Collect evidence exactly as GET /pods/{ns}/{name}/diagnose and POST .../actions/propose do."""
    from app.actions.cluster import ActionsCluster
    from app.actions.service import ActionService
    from app.deps import get_anomaly_scorer, get_k8s_client, get_lifecycle_detector, get_prometheus_client, get_settings
    from app.k8s_client import to_pod_summary
    from app.main import _pod_usage, _score_pod

    from ml.llm_eval.knowledge import retrieve

    k8s = get_k8s_client()
    raw = k8s.get_pod(ns, pod_name)
    pod = to_pod_summary(raw)
    events = k8s.get_pod_events(ns, pod_name)
    logs = k8s.get_pod_logs(ns, pod_name, pod=raw)
    usage = _pod_usage(get_prometheus_client(), ns, pod_name)
    retrieved = retrieve(pod, events, logs, usage)
    anomaly, anomaly_unavailable = _score_pod(
        get_anomaly_scorer(), get_prometheus_client(), ns, pod_name, get_lifecycle_detector()
    )
    cluster = ActionsCluster(k8s)
    pod_state = cluster.pod_state(ns, pod_name)
    deployment_name = pod_state.deployment if pod_state is not None else None
    deployment = cluster.deployment_state(ns, deployment_name) if deployment_name else None
    svc = ActionService(get_settings(), cluster, proposer=None, store=None, lifecycle=get_lifecycle_detector())
    signals = svc._signals(ns, deployment_name)
    return {
        "diagnose_input": {
            "pod": pod.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "logs": logs.model_dump(mode="json"),
            "retrieval_query": retrieved.query,
            "retrieved": [dataclasses.asdict(c) for c in retrieved.chunks],
            "knowledge_unavailable": retrieved.knowledge_unavailable,
            "anomaly": anomaly.model_dump(mode="json") if anomaly is not None else None,
            "anomaly_unavailable": anomaly_unavailable,
            "usage": usage.model_dump(mode="json") if usage is not None else None,
        },
        "propose_input": {
            "deployment": dataclasses.asdict(deployment) if deployment is not None else None,
            "pod_state": dataclasses.asdict(pod_state) if pod_state is not None else None,
            "signals": signals,
        },
    }


def run_case(index: int, case: Case, keep: bool, out_dir: Path, prefix: str = "kc-eval") -> dict:
    ns = f"{prefix}-{index:02d}"
    started = time.time()
    result = {"id": case.id, "namespace": ns, "ok": False}
    try:
        say(case.id, f"setting up in {ns}")
        setup_case(ns, case)
        target = wait_for(lambda: find_target(ns, case), timeout=300, interval=3)
        if target is None:
            states = [(p["metadata"]["name"], p.get("status", {}).get("phase")) for p in pods(ns, case.workload)]
            raise RuntimeError(f"fault never became visible; pods={states}")
        say(case.id, f"fault visible on {target['metadata']['name']} after {time.time() - started:.0f}s; settling {case.settle_s}s")
        time.sleep(case.settle_s)
        target = find_target(ns, case) or target
        pod_name = target["metadata"]["name"]
        context = capture_context(ns, pod_name, case.workload)
        fixture = {
            "schema_version": SCHEMA_VERSION,
            "id": case.id,
            "title": case.title,
            "labels": case.labels(),
            "namespace": ns,
            "workload": case.workload,
            "pod_name": pod_name,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cluster": "kind kube-copilot (single node, Kubernetes v1.32.2)",
            **context,
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{case.id}.json").write_text(json.dumps(fixture, indent=2, default=str) + "\n")
        result.update(ok=True, pod=pod_name, seconds=round(time.time() - started))
        say(case.id, f"captured {pod_name} ({result['seconds']}s)")
    except Exception as exc:  # noqa: BLE001 — report and continue with the other cases
        result["error"] = f"{exc}"
        say(case.id, f"FAILED: {exc}")
        log.debug(traceback.format_exc())
    finally:
        if not keep:
            kubectl("-n", ns, "delete", "podchaos", "--all", "--wait=true", "--timeout=60s", check=False)
            kubectl("delete", "namespace", ns, "--wait=false", check=False)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="Comma-separated case ids (default: all)")
    parser.add_argument("--parallel", type=int, default=8)
    parser.add_argument("--keep", action="store_true", help="Keep the kc-eval-* namespaces")
    parser.add_argument("--set", choices=("main", "heldout"), default="main",
                        help="main: cases.py (38 incidents); heldout: cases_heldout.py")
    parser.add_argument("--out", type=Path, default=None,
                        help="Default: ml/data/llm_eval/cases_v2 (main) or ml/data/llm_eval/heldout")
    parser.add_argument("--relabel", action="store_true",
                        help="Only rewrite the labels of existing fixtures from cases.py (no cluster access)")
    args = parser.parse_args(argv)
    if args.set == "heldout":
        from ml.llm_eval.cases_heldout import HELDOUT_CASES as cases
        prefix = "kc-ho"
    else:
        cases, prefix = CASES, "kc-eval"
    if args.out is None:
        args.out = DATA_DIR / ("heldout" if args.set == "heldout" else "cases_v2")
    if args.relabel:
        by_id = {c.id: c for c in cases}
        for path in sorted(args.out.glob("*.json")):
            data = json.loads(path.read_text())
            if data["id"] in by_id and data["labels"] != by_id[data["id"]].labels():
                data["labels"] = by_id[data["id"]].labels()
                path.write_text(json.dumps(data, indent=2, default=str) + "\n")
                print(f"relabelled {data['id']}")
        return 0
    logging.basicConfig(level=logging.WARNING)

    wanted = {s for s in args.only.split(",") if s}
    indexed = [(i + 1, c) for i, c in enumerate(cases) if not wanted or c.id in wanted]
    if wanted - {c.id for _, c in indexed}:
        print(f"unknown case ids: {sorted(wanted - {c.id for _, c in indexed})}", file=sys.stderr)
        return 2
    from ml.llm_eval.knowledge import get_retriever

    get_retriever()  # build the index once, before the threads start
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda ic: run_case(ic[0], ic[1], args.keep, args.out, prefix), indexed))
    failed = [r for r in results if not r["ok"]]
    print(f"\ncaptured {len(results) - len(failed)}/{len(results)} cases into {args.out}")
    for r in failed:
        print(f"  FAILED {r['id']}: {r.get('error')}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
