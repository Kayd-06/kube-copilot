# LLM eval dataset: frozen Kubernetes incidents

38 incidents, each reproduced on the single-node kind cluster (`kube-copilot`,
Kubernetes v1.32.2) and frozen as one JSON file. Each file holds exactly what
the API sends to the LLM, so evals replay offline and give the same inputs
every time. There are three directories:

| Directory | What it is | Used for |
|---|---|---|
| `cases/` (v1, schema 1) | The 38 incidents as captured for PR #31, unchanged | The "before" results in `ml/reports/llm_eval/*.json` |
| `cases_v2/` (schema 2) | The same 38 incidents. 16 were re-captured to get the new context fields, and the other 22 are byte-identical copies of v1 | The "after" results in `ml/reports/llm_eval/v2/` (default for `make eval`) |
| `heldout/` (schema 2) | 12 new incidents written and captured after the prompt changes were frozen | `ml/reports/llm_eval/heldout/` (`make eval-heldout`) |

### v3 (PR #34): dependency check, rollback traps

| Directory | What it is | Used for |
|---|---|---|
| `cases_v3/` (schema 3) | The 38 incidents. 3 were re-captured (`stale_dependency`, `svc_port_mismatch`, `dns_missing_service`), the other 35 are v2 copies | `ml/reports/llm_eval/v3/` (default for `make eval`) |
| `heldout_v3/` (schema 3) | The 12 held-out incidents plus **5 rollback traps**. `ho_dns_proxy_first` and the 5 traps were captured for v3, the other 11 are copies | `ml/reports/llm_eval/heldout_v3/` (`make eval-heldout`) |
| `traps_before/` | The 5 traps as the PR #33 code sees them: no dependency field, retrieval re-run against the PR #33 runbooks | `ml/reports/llm_eval/traps_before/` |

Schema 3 adds `diagnose_input.dependencies`. These are the in-cluster
Services named by literal env values in the pod (URL or `host:port`), each
with whether it exists, how many backing pods are Ready, when they became
Ready, and whether the Service `targetPort` matches a port the pods declare.
The check only reads the captured pod's own env. For the copied fixtures
that env names no Service, so they got `dependencies: []`, the value the
check would return (`test_v3_dependency_field_matches_the_scenarios`).

Retrieval was **re-run for every v3 fixture** against the current runbooks
(`python -m ml.llm_eval.capture --reretrieve`). That adds the new
`stale-dependency.md` and the edited probe and CPU-throttling runbooks. It
uses the fixture's own pod, events, logs and usage, so no cluster is needed.

The v3 re-captures ran on a rebuilt box. Its host has no `statistic` iptables
match, so kube-proxy only works when every Service has at most one endpoint.
CoreDNS was scaled to 1 replica for the captures, and no re-captured case
uses a multi-endpoint Service.

**Rollback traps.** The 5 traps (`build_traps` in `cases_heldout.py`) each
have a healthy previous revision that differs only in one harmless env
value. The incident then comes from somewhere else, and the previous revision
would fail the same way:

- an upstream Deployment scaled to 0,
- a mounted ConfigMap edited to an invalid value that the app hot-reloads,
- the only node cordoned and the pod deleted,
- a Secret deleted, after which the pod is replaced,
- a healthy pod whose logs are full of retries that succeed.

All 5 expect `no_action`. Their labels and the hand-check rubric
(`trap_rubric.md`) were committed before any model saw them. A ResourceQuota
trap was considered and left out: a quota rejects pods at admission, so
there is no incident pod for Diagnose to look at.

### What changed in v2

Schema 2 adds three things the fixes need:

- `diagnose_input.usage`: per-container CPU use, the CFS-throttled share of
  periods, and memory, with their limits (Prometheus, 5m window).
- `diagnose_input.pod.containers[].resources`: requests and limits from the
  pod spec.
- `propose_input.deployment.template_diff` / `template_refs`: a field-level
  diff of the full pod template between the previous and the current
  ReplicaSet revision (env, envFrom, probes, resources, nodeSelector,
  affinity, volumes, ConfigMap/Secret refs), plus the ConfigMaps, Secrets and
  PVCs the current template references.

The fields can't be derived from v1 files, so the cases that need them were
re-run on the cluster with the **same scenario code and the same labels**
(`cases.py` was not changed). These are the 15 cases with a previous revision
and `cpu_throttle_first`. Re-capturing also refreshes everything else in those
16 files: pod names, timestamps, logs, events, retrieval and the anomaly score
(the Isolation Forest verdict came out the same as in v1 on all 16; the scores
and feature values differ). So part of
any v1 to v2 difference on those 16 cases comes from the new capture, not the
new prompts. The other 22 cases are unchanged, which keeps that confound small.
The new fields are absent there and the prompts simply leave those sections
out.

### Held-out set

The prompt and context changes were made **after** reading the v1 failures,
so the v2 numbers on the original 38 are optimistic. To check whether the
fixes generalise, 12 new incidents ([`cases_heldout.py`](../../llm_eval/cases_heldout.py))
were written and labelled from the injected fault, committed, and only then
captured and scored. Nothing was tuned on them. They reuse the fault families
with different workloads, values and mechanics: a quay.io image, an unknown
CLI flag, a malformed `DATABASE_URL`, an HTTP readiness probe, a 512Gi memory
request, a rollout that cuts a CPU limit to 15m, a 48Mi OOM, two pod kills on 3
replicas, a healthy worker with a CPU limit, a missing Secret volume, a missing
ConfigMap key, and a Service name that doesn't resolve. 7 expect
`rollback_deployment` and 5 expect `no_action`.

The "old code" baseline on the held-out set uses the same files
reduced to schema 1, with retrieval redone against the v1 runbooks (no CPU
throttling runbook), and is run from the PR #31 merge commit (`cc30707`). Its reports are in
`ml/reports/llm_eval/heldout_before/` and its hand checks in
`hand_checks_heldout_before.json`.

- `diagnose_input`: pod summary, events, logs (current and previous), the
  retrieval query and the top-5 runbook chunks it returned, and the anomaly
  score with lifecycle signals. All of it was collected with the API's own code
  paths (`K8sClient`, `Retriever`, `_score_pod`).
- `propose_input`: the owning Deployment's current and previous revision
  (`ActionsCluster.deployment_state`), the pod state, and the disruption
  signals the safe-actions proposer sees.
- `labels`: the root-cause category, the acceptable remediations (safe-actions
  tool names, including `no_action`), the runbooks that cover the fault, the
  difficulty, and a plain description of the injected fault.

## How the labels were made

Each case is a scenario in [`ml/llm_eval/cases.py`](../../llm_eval/cases.py).
The label was written from the **fault we injected**, before any model saw the
case, and never from model output. Two labels were corrected after capture,
and the reasons are in git history and the case `notes`:

- `init_migration_first` was moved to *hard*. Diagnose only fetches main-container
  logs, so the migration error never reaches the model.
- `stale_dependency` was redesigned. The first version, a crashloop on a stale
  env value, healed itself because the kubelet re-reads ConfigMap env on every
  container restart, so its label would have been wrong. It is now an app that
  checks its upstream only at startup and stays not-Ready after the upstream
  recovers.

Rules used for remediation labels:

- **`rollout` cases** first deploy a healthy revision and then a broken one, so
  `rollback_deployment` is the fix.
- **`first` cases** are broken on revision 1. None of the allowed actions fixes
  them, so the expected answer is `no_action` (a human must change the spec).
- **Four cases have a healthy previous revision, but rolling back would not
  help**: a shared ConfigMap was deleted or edited after the rollout, a replica
  was killed after it, or a routine rollout simply succeeded. These stop the
  heuristic "roll back whenever you can" from looking perfect.
- `cpu_throttle_first` and `pending_antiaffinity_first` also accept
  `scale_deployment`. `stale_dependency` accepts `restart_deployment` or
  `delete_pod`.

## Composition

| Category | Cases | Includes |
|---|---|---|
| missing_config | 5 | missing ConfigMap (envFrom, volume), missing Secret, missing Secret key, ConfigMap deleted after rollout |
| app_crash | 4 | missing binary, Python traceback, Go panic, failing init-container migration |
| probe_failure | 4 | liveness 404, readiness wrong port, liveness kills a slow starter, exec readiness never passes |
| healthy | 4 | nginx, worker with routine WARN logs, completed Job, right after a routine rollout |
| image_pull | 3 | bad tag, registry host that does not resolve, repository that does not exist |
| oom_killed | 3 | load exceeds limit, rollout lowers limit, slow leak |
| unschedulable | 3 | 64-CPU request, unmatched nodeSelector, required anti-affinity on one node |
| bad_config_value | 3 | invalid `PORT`, invalid `LOG_LEVEL` in a ConfigMap, ConfigMap edited after rollout |
| dependency_connectivity | 3 | Service targetPort mismatch, Service name that does not resolve, app stuck after its upstream recovered |
| pod_disruption | 3 | one kill, three kills in 90 s, a kill after a rollout (Chaos Mesh `PodChaos`) |
| pvc_pending | 2 | unknown StorageClass, PVC that does not exist |
| cpu_throttling | 1 | CPU-bound worker under a 20m limit |

By difficulty, 17 cases are easy, 10 medium and 11 hard. By first acceptable
action, 26 are `no_action`, 11 `rollback_deployment` and 1 `restart_deployment`.
15 cases have a previous revision, and 29 have lifecycle signals in the context.

## What is real and what is not

- **Real**: all of the Kubernetes behaviour. That covers scheduling, image
  pulls, OOM kills, probe results, restarts, back-off, events, Chaos Mesh pod
  kills, Prometheus metrics, Isolation Forest scores and lifecycle signals.
- **Not real**: the workloads themselves. They are tiny busybox, nginx and curl
  containers whose shell scripts behave like a small service. The log lines
  that stand in for an application (a traceback, a panic, a validation error,
  `processed batch N`) were written as part of each fault, but the
  infrastructure messages (curl errors, kubelet and scheduler events) are what
  the cluster really produced.
- Namespaces are `kc-eval-NN` and the workload names are neutral, so nothing in
  the context gives away the label. A test checks this.
- The anomaly model is the API default (synthetic-trained Isolation Forest).
  It flags several healthy pods, which is realistic noise for Diagnose.

## Re-capturing

```bash
make cluster-up metrics-up chaos-up && make metrics-forward   # Prometheus on :9090
make eval-capture                       # all cases into cases_v2/, about 10 minutes, 8 at a time
make eval-capture ONLY=oom_first,healthy_web
make eval-capture-heldout               # the 12 held-out cases into heldout/
```

Every case runs in its own `kc-eval-NN` namespace (label
`kube-copilot.io/eval=true`), which is deleted afterwards. Re-capturing changes
pod names, timestamps and metrics, so re-run `make eval` afterwards.
