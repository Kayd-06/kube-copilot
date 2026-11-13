"""Real end-to-end check of safe actions on the kind cluster.

Injects a fault into copilot-demo/web, asks the running backend for a proposal
(real LLM), approves the first proposed action over the API, and waits for the
backend's verification. Recovery is also timed independently with kubectl.

Usage (backend running with ACTIONS_ENABLED=true on :8010):
    python scripts/e2e_safe_actions.py --api http://localhost:8010 --out docs/reports/safe_actions_e2e.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

NS, DEP = "copilot-demo", "web"
MANIFEST = Path(__file__).resolve().parents[1] / "demo" / "actions" / "web.yaml"


def kubectl(*args: str, check: bool = True) -> str:
    return subprocess.run(["kubectl", *args], check=check, capture_output=True, text=True).stdout


def pods() -> list[dict]:
    return json.loads(kubectl("-n", NS, "get", "pods", "-l", f"app={DEP}", "-o", "json"))["items"]


def bad_pods() -> list[str]:
    out = []
    for p in pods():
        if p["metadata"].get("deletionTimestamp"):
            continue
        for cs in p["status"].get("containerStatuses", []):
            reason = (cs.get("state", {}).get("waiting") or {}).get("reason")
            if reason in {"CrashLoopBackOff", "ErrImagePull", "ImagePullBackOff"} or (
                cs.get("restartCount", 0) >= 2 and not cs.get("ready")
            ):
                out.append(p["metadata"]["name"])
    return out


def healthy() -> bool:
    d = json.loads(kubectl("-n", NS, "get", "deploy", DEP, "-o", "json"))
    want = d["spec"]["replicas"]
    st = d.get("status", {})
    return (
        st.get("observedGeneration", 0) >= d["metadata"]["generation"]
        and st.get("updatedReplicas", 0) == want
        and st.get("readyReplicas", 0) == want
        and st.get("availableReplicas", 0) == want
        and st.get("replicas", 0) == want
        and not bad_pods()
    )


def reset() -> None:
    kubectl("-n", NS, "patch", "deploy", DEP, "--type=merge", "-p",
            '{"spec":{"strategy":{"type":"RollingUpdate","rollingUpdate":{"maxUnavailable":"25%","maxSurge":"25%"}}}}',
            check=False)
    kubectl("apply", "-f", str(MANIFEST))
    kubectl("-n", NS, "patch", "configmap", "web-config", "-p", '{"data":{"UPSTREAM_READY":"true"}}')
    kubectl("-n", NS, "rollout", "status", f"deploy/{DEP}", "--timeout=120s")
    deadline = time.time() + 120
    while not healthy() and time.time() < deadline:
        time.sleep(2)


# ---------------------------------------------------------------- faults
def fault_bad_image() -> None:
    kubectl("-n", NS, "set", "image", f"deploy/{DEP}", "web=nginx:1.27-alpine-typo")


def fault_bad_command() -> None:
    patch = [{"op": "replace", "path": "/spec/template/spec/containers/0/args",
              "value": ["echo 'config parse error: unknown key listen_port'; exit 2"]}]
    kubectl("-n", NS, "patch", "deploy", DEP, "--type=json", "-p", json.dumps(patch))


def fault_stale_dependency() -> None:
    # The dependency is down during a rollout, then comes back; new pods stay in
    # CrashLoopBackOff with the stale env until something restarts them.
    kubectl("-n", NS, "patch", "configmap", "web-config", "-p", '{"data":{"UPSTREAM_READY":"false"}}')
    kubectl("-n", NS, "rollout", "restart", f"deploy/{DEP}")
    deadline = time.time() + 120
    while time.time() < deadline:
        if any("CrashLoopBackOff" in json.dumps(p["status"]) for p in pods()):
            break
        time.sleep(2)
    kubectl("-n", NS, "patch", "configmap", "web-config", "-p", '{"data":{"UPSTREAM_READY":"true"}}')


def fault_outage_bad_image() -> None:
    # Recreate strategy: every old pod is killed first, so the bad image is a full outage.
    patch = {"spec": {"strategy": {"type": "Recreate", "rollingUpdate": None},
                      "template": {"spec": {"containers": [{"name": "web", "image": "nginx:1.27-alpine-typo"}]}}}}
    kubectl("-n", NS, "patch", "deploy", DEP, "-p", json.dumps(patch))


FAULTS = {"bad_image": fault_bad_image, "outage_bad_image": fault_outage_bad_image, "bad_command": fault_bad_command, "stale_dependency": fault_stale_dependency}


# ---------------------------------------------------------------- trial
def run_trial(api: httpx.Client, scenario: str, approver: str) -> dict:
    trial: dict = {"scenario": scenario, "started_at": datetime.now(timezone.utc).isoformat()}
    reset()
    t_inject = time.time()
    FAULTS[scenario]()
    bad: list[str] = []
    while time.time() - t_inject < 180:
        bad = bad_pods()
        if bad:
            break
        time.sleep(1)
    if not bad:
        trial["outcome"] = "fault_not_observed"
        return trial
    trial["fault_visible_after_s"] = round(time.time() - t_inject, 1)
    trial["incident_pod"] = bad[0]

    t0 = time.time()
    r = api.post(f"/pods/{NS}/{bad[0]}/actions/propose", json={"actor": approver})
    trial["propose_latency_s"] = round(time.time() - t0, 2)
    r.raise_for_status()
    body = r.json()
    trial["note"] = body.get("note")
    trial["proposals"] = [
        {"id": a["id"], "kind": a["kind"], "params": a["params"], "status": a["status"],
         "violations": a["violations"], "rationale": a["rationale"]}
        for a in body["actions"]
    ]
    chosen = next((a for a in body["actions"] if a["status"] == "proposed"), None)
    if chosen is None:
        trial["outcome"] = "no_approvable_proposal"
        return trial
    trial["approved_kind"] = chosen["kind"]

    # Cooldown is a real guardrail; record it if it bites, then wait it out.
    while True:
        t_approve = time.time()
        r = api.post(f"/actions/{chosen['id']}/approve", json={"actor": approver, "comment": "e2e"})
        if r.status_code == 429:
            trial.setdefault("rate_limited", []).append(r.json()["detail"])
            time.sleep(10)
            continue
        break
    r.raise_for_status()
    # Approving twice must be refused.
    trial["double_approve_status"] = api.post(f"/actions/{chosen['id']}/approve", json={"actor": approver}).status_code

    recovered_at = None
    status = r.json()["status"]
    while time.time() - t_approve < 300:
        if recovered_at is None and healthy():
            recovered_at = time.time()
        status = api.get(f"/actions/{chosen['id']}").json()["status"]
        if status not in {"executing", "verifying"} and recovered_at is not None:
            break
        if status not in {"executing", "verifying"} and time.time() - t_approve > 200:
            break
        time.sleep(1)
    detail = api.get(f"/actions/{chosen['id']}").json()
    v = detail.get("verification") or {}
    trial.update(
        outcome=status,
        kubectl_recovery_after_approve_s=round(recovered_at - t_approve, 1) if recovered_at else None,
        verifier_time_to_recovery_s=v.get("time_to_recovery_seconds"),
        verifier_reason=v.get("reason"),
        verifier_samples=len(v.get("samples", [])),
        metric_anomalies_after=v.get("metric_anomalies"),
        timeline=[e["event"] for e in detail.get("timeline", [])],
        total_fault_to_recovery_s=round(recovered_at - t_inject, 1) if recovered_at else None,
    )
    return trial


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--out", default="docs/reports/safe_actions_e2e.json")
    parser.add_argument("--scenarios", default="bad_image,bad_command,stale_dependency")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--append", action="store_true", help="add trials to an existing report")
    args = parser.parse_args()

    api = httpx.Client(base_url=args.api, timeout=120)
    config = api.get("/actions/config").json()
    report: dict = {"config": config, "trials": [], "guardrail_checks": {}}
    if args.append and Path(args.out).exists():
        report = json.loads(Path(args.out).read_text())

    # Live guardrail: a denied namespace is refused before the LLM is called.
    r = api.post("/pods/kube-system/coredns-anything/actions/propose")
    report["guardrail_checks"]["kube_system_propose"] = {"status": r.status_code, "body": r.json()}

    for scenario in args.scenarios.split(","):
        for i in range(args.trials):
            trial = run_trial(api, scenario, approver="e2e-script")
            trial["trial"] = i + 1
            print(json.dumps({k: trial.get(k) for k in (
                "scenario", "trial", "approved_kind", "outcome", "propose_latency_s",
                "kubectl_recovery_after_approve_s", "verifier_time_to_recovery_s", "note")}), flush=True)
            report["trials"].append(trial)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(report, indent=2))
    reset()


if __name__ == "__main__":
    main()
