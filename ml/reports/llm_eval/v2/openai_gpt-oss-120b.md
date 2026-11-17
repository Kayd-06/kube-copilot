# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-14T08:46:41+00:00 → 2026-11-15T04:07:08+00:00 (UTC) on 38 frozen incidents from `ml/data/llm_eval/cases_v2/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 35/38 = 92.1% (95% CI (79.2, 97.3)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.66s / 2.57s |
| Mean confidence when right / wrong | 0.919 / 0.89 |
| Remediation (first proposed action) correct | 37/38 = 97.4% (95% CI (86.5, 99.5)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/12 |
| Propose latency p50 / p95 | 1.03s / 2.21s |
| RAG: correct runbook in top-5 | 86.2% of 29 cases (hit@1 72.4%, MRR 0.76) |

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 55.3% |
| Remediation: always `no_action` | 68.4% |
| Remediation: rollback if a previous revision exists, else `no_action` | 86.8% |

## All runs

| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| diagnose/full | 38 | 0 | 92.1% | 1.66s | 2.57s | 99013 | 21270 | 0.0276 |
| propose/full | 38 | 0 | 97.4% | 1.03s | 2.21s | 64680 | 11235 | 0.0164 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 3/4 | 4/4 |
| bad_config_value | 3/3 | 3/3 |
| cpu_throttling | 1/1 | 1/1 |
| dependency_connectivity | 2/3 | 2/3 |
| healthy | 3/4 | 4/4 |
| image_pull | 3/3 | 3/3 |
| missing_config | 5/5 | 5/5 |
| oom_killed | 3/3 | 3/3 |
| pod_disruption | 3/3 | 3/3 |
| probe_failure | 4/4 | 4/4 |
| pvc_pending | 2/2 | 2/2 |
| unschedulable | 3/3 | 3/3 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 16/17 | 17/17 |
| hard | 9/11 | 10/11 |
| medium | 10/10 | 10/10 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `cm_deleted_after_rollout` | missing_config | Missing ConfigMap pricing-config referenced by the pod spec, causing a CreateContainerConfigError. | ✅ | no_action | no_action | ✅ |
| `cm_edited_after_rollout` | bad_config_value | The container’s command/args (or environment) passes an invalid log level "verbose" that the busybox‑based ent… | ✅ | no_action | no_action | ✅ |
| `config_loglevel_first` | bad_config_value | Container command/args (or image entrypoint) passes an invalid log‑level flag, causing the process to exit wit… | ✅ | no_action | no_action | ✅ |
| `config_port_rollout` | bad_config_value | The PORT environment variable is set to a non‑numeric string ("eighty"), causing the container process to exit… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `cpu_throttle_first` | cpu_throttling | CPU throttling caused by an overly restrictive CPU limit (20m) for the thumbnailer container. | ✅ | no_action / scale_deployment | no_action | ✅ |
| `crash_bad_command_rollout` | app_crash | Container command points to a non‑existent binary (/app/server) in the busybox:1.36 image, causing the contain… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_panic_rollout` | app_crash | Application code bug – Router is nil when Register is called, causing a nil‑pointer panic and container exit. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_traceback_first` | app_crash | Application code raises an unhandled KeyError because the job type 'invoice.v2' is not present in the HANDLERS… | ❌ | no_action | no_action | ✅ |
| `dns_missing_service` | dependency_connectivity | DNS/connection failure to the pricing‑api service causing the container to exit immediately. | ✅ | no_action | no_action | ✅ |
| `healthy_after_rollout` | healthy | False‑positive anomaly detection flag; the pod is healthy. | ✅ | no_action | no_action | ✅ |
| `healthy_job_completed` | healthy | The pod behaved as intended – it ran its batch task and terminated with a successful exit code; there is no fa… | ✅ | no_action | no_action | ✅ |
| `healthy_noisy_logs` | healthy | Repeated upstream service timeouts causing retries and low cache hit ratio; the downstream dependency is unava… | ❌ | no_action | no_action | ✅ |
| `healthy_web` | healthy | False‑positive anomaly detection flag; the pod is operating normally. | ✅ | no_action | no_action | ✅ |
| `img_private_rollout` | image_pull | DNS resolution failure for ghcr.io on the node (CoreDNS or node DNS configuration) causing image pull to fail. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `img_registry_first` | image_pull | DNS resolution failure for the image registry (registry.example.invalid) from the node, causing the image pull… | ✅ | no_action | no_action | ✅ |
| `img_tag_rollout` | image_pull | Incorrect image reference (typo in tag) causing the kubelet to fail pulling the image. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `init_migration_first` | app_crash | The init container run-migrations exits with an error (e.g., missing secret/config, bad command), causing a Cr… | ✅ | no_action | no_action | ✅ |
| `liveness_path_rollout` | probe_failure | Misconfigured liveness probe (path /healthz missing) causing the kubelet to kill the container repeatedly. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `liveness_slow_start_first` | probe_failure | Liveness probe is too aggressive for the container’s startup time, causing the kubelet to kill the container b… | ✅ | no_action | no_action | ✅ |
| `missing_cm_envfrom_rollout` | missing_config | Missing ConfigMap "cart-feature-flags" in namespace kc-eval-10 (or pod spec references a non‑existent ConfigMa… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `missing_cm_volume_first` | missing_config | Missing ConfigMap "search-synonyms" referenced by the pod’s volume mount, causing a FailedMount during pod ini… | ✅ | no_action | no_action | ✅ |
| `missing_secret_first` | missing_config | Missing secret orders-db-credentials referenced by the pod spec. | ✅ | no_action | no_action | ✅ |
| `missing_secret_key_rollout` | missing_config | Missing or mis‑named key (api_token) in the Secret kc-eval-13/ledger-api referenced by the pod’s environment/v… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `oom_first` | oom_killed | Memory limit for the catalog container is too low for the workload, causing OOM kills and subsequent back‑off … | ✅ | no_action | no_action | ✅ |
| `oom_leak_first` | oom_killed | Container memory limit too low, causing an out‑of‑memory kill. | ✅ | no_action | no_action | ✅ |
| `oom_limit_rollout` | oom_killed | Container exceeds its 64 Mi memory limit, triggering OOM kill and CrashLoopBackOff. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `pending_antiaffinity_first` | unschedulable | Strict pod anti‑affinity rule that cannot be satisfied on the single available node, causing FailedScheduling … | ✅ | no_action / scale_deployment | scale_deployment | ✅ |
| `pending_cpu_first` | unschedulable | CPU request of the pod exceeds the available CPU on all nodes (insufficient CPU resources). | ✅ | no_action | no_action | ✅ |
| `pending_nodeselector_rollout` | unschedulable | Node affinity (or nodeSelector) defined in the pod spec does not match any existing node labels, preventing sc… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `pod_kill_after_rollout` | pod_disruption | A deliberate pod disruption (e.g., a rollout, manual delete, or node‑drain) killed the previous replica and th… | ✅ | no_action | no_action | ✅ |
| `pod_kill_repeated` | pod_disruption | External pod disruption that killed and recreated catalog-api pods (likely a manual delete or rolling restart) | ✅ | no_action | no_action | ✅ |
| `pod_kill_single` | pod_disruption | Pod was intentionally replaced (e.g., by a deployment rollout, manual delete, or node drain), not a failure of… | ✅ | no_action | no_action | ✅ |
| `pvc_missing_first` | pvc_pending | Missing or unbound PersistentVolumeClaim "exports-data" referenced by the pod spec. | ✅ | no_action | no_action | ✅ |
| `pvc_storageclass_first` | pvc_pending | The PersistentVolumeClaim required by the pod is unbound (no matching PersistentVolume), preventing scheduling… | ✅ | no_action | no_action | ✅ |
| `readiness_exec_rollout` | probe_failure | Misconfigured readiness probe – the probe checks for a file that the container never creates. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `readiness_port_first` | probe_failure | Readiness probe is misconfigured – it checks the wrong port (8081) that the nginx container does not expose. | ✅ | no_action | no_action | ✅ |
| `stale_dependency` | dependency_connectivity | Readiness probe misconfiguration – the probe expects a file (/tmp/ready) that the container never creates, cau… | ❌ | restart_deployment / delete_pod | no_action | ❌ |
| `svc_port_mismatch` | dependency_connectivity | The upstream inventory service (http://inventory:80) is unavailable or unreachable, causing the curl command t… | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 3619s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
