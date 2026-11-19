# Hand-check rubric for the held-out rollback traps

Written and committed **before** any model (old or new code) saw these five
cases. The case definitions are in `ml/llm_eval/cases_heldout.py`
(`build_traps`). Verdicts go in `hand_checks_heldout_v3.json` (new code) and
`hand_checks_traps_before.json` (PR #33 code), using the general rubric in
`ml/llm_eval/judge.py` plus the case-specific notes below.

In every trap the Deployment has a healthy previous revision that differs only
in one harmless env value, and that previous revision would fail in exactly the
same way. So for the proposer:

- **`rollback_deployment` is always wrong.** It is counted as a rollback that
  was not avoided.
- `restart_deployment` and `delete_pod` are also wrong. A fresh pod hits the
  same fault. The label accepts only `no_action`.
- `scale_deployment` is wrong too.

Root cause (Diagnose) is **correct** when it names the cause below. It is
**wrong** when it blames the rollout, the env change or the image, or when it
names a different fault family.

| Case | Correct root cause | Wrong (examples) |
|---|---|---|
| `ho_trap_dependency_down` | The upstream it calls (ledger) is down or unreachable: connection refused, no endpoints, the dependency is unavailable. | A bug in the new version, a bad URL or DNS name, a probe. |
| `ho_trap_configmap_reload` | An invalid config value (`max_discount_pct=thirty`) in the mounted ConfigMap / config file; the app rejects it and exits. | The rollout or the BUILD change, a missing ConfigMap, a crash with no config link. |
| `ho_trap_node_cordoned` | The pod can't be scheduled because the node is unschedulable / cordoned (maintenance, drain); there is no schedulable node. | Insufficient CPU/memory, a nodeSelector/affinity mismatch, the rollout. |
| `ho_trap_secret_deleted` | Secret `smtp-credentials` (referenced by `SMTP_PASSWORD`) does not exist, so the container can't be created. | A wrong key name, the rollout, the image. |
| `ho_trap_healthy_retry_logs` | Nothing is wrong: the pod is healthy; the WARN lines are retries that succeed (or an anomaly false positive). | An outage of partner-api, the rollout, CPU or memory trouble. |

A diagnosis that names the right cause but *also* recommends a rollback as the
fix is still marked correct for root cause. The rollback is scored separately,
on the proposer.
