# Safe actions: end-to-end results on kind

Recorded on 2026-11-13 on the single-node kind cluster `kube-copilot` (the same box used for the chaos runs). Raw data, including every proposal, rationale and audit timeline: [safe_actions_e2e.json](safe_actions_e2e.json). Reproduce with `make actions-demo-up`, `make backend-dev-actions` and `make actions-e2e`.

## Setup

- Target: `copilot-demo/web`, 2 replicas of `nginx:1.27-alpine` with a readiness probe (`demo/actions/web.yaml`).
- Backend run as a host process with `ACTIONS_ENABLED=true`, `ACTIONS_ALLOWED_NAMESPACES=copilot-demo`, default cooldown (120 s), default stable period (20 s), poll interval lowered to 2 s (`ACTIONS_VERIFY_INTERVAL_SECONDS=2`) for finer timing.
- LLM: Groq `openai/gpt-oss-120b` through the same OpenAI-compatible client. The project default, `llama-3.3-70b-versatile`, returned HTTP 404 ("does not exist or you do not have access to it") for this key on the test day, so it could not be used.
- Harness (`scripts/e2e_safe_actions.py`): reset the Deployment, inject a fault, wait until a pod is in `ErrImagePull`/`ImagePullBackOff`/`CrashLoopBackOff`, call `POST /pods/{ns}/{pod}/actions/propose`, approve the first approvable proposal over the API, then wait for the backend's verdict. Recovery is also timed independently by polling the Deployment with `kubectl` every 1 s (`updated == ready == available == desired`, no bad pods).
- 3 trials per scenario, 12 trials in total.

## Scenarios

| Scenario | Fault | Old pods keep serving? |
|---|---|---|
| `bad_image` | `kubectl set image` to a tag that does not exist | Yes (RollingUpdate) |
| `bad_command` | Rollout with a container command that exits 2 | Yes (RollingUpdate) |
| `outage_bad_image` | Same bad image, with `strategy: Recreate`, so every old pod is killed first | **No: full outage** |
| `stale_dependency` | ConfigMap `UPSTREAM_READY=false` during a restart, then set back to `true`; new pods are in CrashLoopBackOff | Yes (RollingUpdate) |

## Results

| Scenario | LLM proposal (3 trials) | Outcome | Propose latency, median | Approve → healthy (kubectl), median [runs] | Verifier time to recovery, median [runs] |
|---|---|---|---|---|---|
| `bad_image` | `rollback_deployment` ×3 | resolved 3/3 | 1.06 s | 0.2 s [0.1, 0.2, 0.2] | 2.0 s [2.0, 2.0, 2.0] |
| `bad_command` | `rollback_deployment` ×3 | resolved 3/3 | 1.00 s | 0.2 s [0.2, 0.2, 0.2] | 2.0 s [2.0, 2.0, 2.0] |
| `outage_bad_image` | `rollback_deployment` ×3 | resolved 3/3 | 1.07 s | 3.4 s [11.7, 3.4, 3.3] | 4.0 s [12.1, 4.0, 4.0] |
| `stale_dependency` | `no_action` ×3 | nothing to approve | 1.13 s | — | — |

How to read this:

- **All 9 approved actions were resolved**, and each matched what a person would have done (roll back the bad rollout). The verifier and the independent `kubectl` check agreed every time; the verifier is at most one poll interval (2 s) later.
- For `bad_image` and `bad_command`, the old ReplicaSet was still serving, so a rollback only has to scale the old pods back to "current" and delete the bad one. That is why recovery is under a second; there was no user-facing outage to recover from.
- `outage_bad_image` is the real outage case: rollback has to start new pods and pass the readiness probe. Recovery after approval took 3.3 to 11.7 s. The 11.7 s run was the first one; the cause of the slower first run was not investigated.
- `stale_dependency`: the model declined all three times, saying the crash comes from `UPSTREAM_READY` and needs a human to fix the config. It could not see that the ConfigMap had already been fixed (the evidence it gets is the pod, events, logs and the Deployment, not ConfigMaps), so a restart, which would have helped, was not proposed. Conservative, but a miss.
- Time from fault injection to a visible bad pod was 4.3 to 10.7 s (image pull back-off is slower than a crash). End-to-end "fault to recovery" is not reported as a headline number, because the harness spent most of it waiting out the per-workload cooldown between trials (it hit the 120 s cooldown 67 times; each was recorded in the audit log as `rate_limited`).

## Same flow with only the chart's RBAC

To check that the Helm RBAC is enough (and no more), the chart's ServiceAccount, ClusterRole and the `actions.enabled=true` Role/RoleBinding for `copilot-demo` were applied to the cluster, and the backend was run with a token for that ServiceAccount instead of the admin kubeconfig. `kubectl auth can-i` for that account: patch deployments, patch deployments/scale and delete pods in `copilot-demo` → yes; the same verbs in `kube-system` or `chaos-lab` → no; create/update/delete deployments and get secrets in `copilot-demo` → no.

One trial each ([safe_actions_e2e_rbac.json](safe_actions_e2e_rbac.json)): `outage_bad_image` → `rollback_deployment`, resolved, healthy 10.7 s after approval (verifier 10.1 s); `bad_command` → `rollback_deployment`, resolved, 0.2 s (verifier 2.0 s). Dry runs and the real patches both worked with these permissions. As in the main run, the first full-outage rollback of the session was the slow one.

## Guardrails exercised live

- `POST /pods/kube-system/<pod>/actions/propose` → refused before the LLM was called (`proposal_refused`, "namespace 'kube-system' is on the deny list").
- A second approval of an already-approved action → HTTP 409 in all 9 trials.
- Approving a new proposal for `web` within 120 s of the last execution → HTTP 429 (cooldown), 67 times across the run.
- Every proposal was dry-run on the API server first (`dryRun=All`) and shown as a diff before approval.
- Audit log for the run: 14 `proposal_requested`, 2 `proposal_refused`, 9 `proposed`, 3 `no_action_proposed`, 9 `approved`, 9 `executed`, 9 `verification_started`, 9 `verified_resolved`, 67 `rate_limited`.

Hallucinated tools, out-of-bounds scaling, other workloads, the kill switch, expiry, policy re-check at approval and reverts are covered by the unit and API tests (`backend/tests/test_actions_policy.py`, `test_actions_api.py`). The LLM never proposed a disallowed action during this run, so those paths were not hit live.

## Caveats

- One single-node kind cluster, one small nginx Deployment, 3 trials per scenario. These are sanity numbers, not a benchmark.
- The faults are simple and the evidence makes the fix obvious (the bad image tag is in the template diff the model sees). Harder incidents were not tested.
- Model: `openai/gpt-oss-120b`, not the project default (see Setup).
- The Isolation Forest attached as `metric_anomalies` flagged the healthy `web` pods after every fix. The backend loaded the default synthetic-trained model, which is known to over-flag real pods (see `ml/reports/real_metrics.md`). It is informational only and did not affect the verdict.
- No authentication: the approver name is self-declared (`e2e-script` here).
