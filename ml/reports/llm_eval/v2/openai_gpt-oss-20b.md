# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-14T08:32:11+00:00 → 2026-11-15T03:48:54+00:00 (UTC) on 38 frozen incidents from `ml/data/llm_eval/cases_v2/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 33/38 = 86.8% (95% CI (72.7, 94.2)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 5.3% of diagnoses |
| Diagnose latency p50 / p95 | 1.09s / 1.61s |
| Mean confidence when right / wrong | 0.902 / 0.93 |
| Remediation (first proposed action) correct | 38/38 = 100.0% (95% CI (90.8, 100.0)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/13 |
| Propose latency p50 / p95 | 0.93s / 3.98s |
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
| diagnose/full | 38 | 0 | 86.8% | 1.09s | 1.61s | 99013 | 25610 | 0.0151 |
| propose/full | 38 | 0 | 100.0% | 0.93s | 3.98s | 64680 | 18517 | 0.0104 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 3/4 | 4/4 |
| bad_config_value | 2/3 | 3/3 |
| cpu_throttling | 1/1 | 1/1 |
| dependency_connectivity | 2/3 | 3/3 |
| healthy | 2/4 | 4/4 |
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
| easy | 14/17 | 17/17 |
| hard | 10/11 | 11/11 |
| medium | 9/10 | 10/10 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `cm_deleted_after_rollout` | missing_config | Missing ConfigMap "pricing-config" referenced by the pod spec. | ✅ | no_action | no_action | ✅ |
| `cm_edited_after_rollout` | bad_config_value | The container is launched with an unsupported log level ("verbose"), causing it to terminate immediately. | ✅ | no_action | no_action | ✅ |
| `config_loglevel_first` | bad_config_value | The application is started with an unsupported log level (e.g., LOG_LEVEL=verbose) that causes it to terminate… | ✅ | no_action | no_action | ✅ |
| `config_port_rollout` | bad_config_value | The PORT env var is set to the string "eighty" instead of a valid integer, leading to a fatal startup error. | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `cpu_throttle_first` | cpu_throttling | CPU limit too low for the workload, leading to constant throttling. | ✅ | no_action / scale_deployment | no_action | ✅ |
| `crash_bad_command_rollout` | app_crash | Missing executable /app/server in the container image (image mismatch or wrong command). | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_panic_rollout` | app_crash | Application bug (nil pointer dereference in router.Register) triggered at container start, likely due to missi… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_traceback_first` | app_crash | The application code is missing a handler for the 'invoice.v2' job type, causing a KeyError and process exit. | ❌ | no_action | no_action | ✅ |
| `dns_missing_service` | dependency_connectivity | DNS resolution failure for the upstream service "pricing-api" (service missing or endpoints empty). | ✅ | no_action | no_action | ✅ |
| `healthy_after_rollout` | healthy | Anomaly detector false positive due to low CPU/memory usage of a healthy Nginx container. | ✅ | no_action | no_action | ✅ |
| `healthy_job_completed` | healthy | None – the pod finished as intended. | ❌ | no_action | no_action | ✅ |
| `healthy_noisy_logs` | healthy | Anomaly detector false positive – the pod is functioning correctly and the high score is a model mis‑classific… | ✅ | no_action | no_action | ✅ |
| `healthy_web` | healthy | False positive from the anomaly detection model. | ❌ | no_action | no_action | ✅ |
| `img_private_rollout` | image_pull | Node kube-copilot-control-plane has a DNS resolution problem for ghcr.io, preventing image pulls. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `img_registry_first` | image_pull | DNS resolution failure for registry.example.invalid on the node kube-copilot-control-plane. | ✅ | no_action | no_action | ✅ |
| `img_tag_rollout` | image_pull | The container image tag "1.27-alpine-typo" is incorrect or does not exist in the registry, causing the image p… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `init_migration_first` | app_crash | The container’s entrypoint/command fails on startup (likely due to a mis‑configured script, missing environmen… | ✅ | no_action | no_action | ✅ |
| `liveness_path_rollout` | probe_failure | Misconfigured liveness probe (incorrect path or too aggressive) causing the container to be killed even though… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `liveness_slow_start_first` | probe_failure | Liveness probe is too aggressive (initialDelaySeconds/failureThreshold too low) for the nginx container’s star… | ✅ | no_action | no_action | ✅ |
| `missing_cm_envfrom_rollout` | missing_config | Missing ConfigMap "cart-feature-flags" referenced by the pod spec. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `missing_cm_volume_first` | missing_config | Missing ConfigMap "search-synonyms" referenced as a volume. | ✅ | no_action | no_action | ✅ |
| `missing_secret_first` | missing_config | The secret "orders-db-credentials" referenced by the pod does not exist in the kc-eval-11 namespace. | ✅ | no_action | no_action | ✅ |
| `missing_secret_key_rollout` | missing_config | Missing key 'api_token' in Secret 'ledger-api' referenced by the pod spec. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `oom_first` | oom_killed | The container’s memory limit is too low for the workload, causing the kubelet to OOM‑kill the process. | ✅ | no_action | no_action | ✅ |
| `oom_leak_first` | oom_killed | Memory limit too low for the ingest process, causing OOM kills. | ✅ | no_action | no_action | ✅ |
| `oom_limit_rollout` | oom_killed | The memory limit is too low for the workload’s actual memory usage, leading to OOM kills. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `pending_antiaffinity_first` | unschedulable | Pod anti‑affinity rule conflicts with existing session‑cache pods, leaving no eligible node. | ✅ | no_action / scale_deployment | scale_deployment | ✅ |
| `pending_cpu_first` | unschedulable | CPU request exceeds the allocatable CPU on all available nodes. | ✅ | no_action | no_action | ✅ |
| `pending_nodeselector_rollout` | unschedulable | The pod’s nodeSelector or nodeAffinity rules are too restrictive and do not match any node labels in the clust… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `pod_kill_after_rollout` | pod_disruption | External disruption (Killing event) caused the previous pod to be terminated and replaced by the ReplicaSet. | ✅ | no_action | no_action | ✅ |
| `pod_kill_repeated` | pod_disruption | External pod disruption (Killing events) caused the pod to be replaced; the pod itself is healthy. | ✅ | no_action | no_action | ✅ |
| `pod_kill_single` | pod_disruption | The previous pod was killed by an external disruption (likely a node drain or eviction) and replaced by the Re… | ✅ | no_action | no_action | ✅ |
| `pvc_missing_first` | pvc_pending | Missing or mis‑named PersistentVolumeClaim "exports-data" referenced by the pod. | ✅ | no_action | no_action | ✅ |
| `pvc_storageclass_first` | pvc_pending | The pod’s PersistentVolumeClaim is pending and has not been bound to a PersistentVolume. | ✅ | no_action | no_action | ✅ |
| `readiness_exec_rollout` | probe_failure | Readiness probe is misconfigured – it expects a file that the container never creates. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `readiness_port_first` | probe_failure | Readiness probe is misconfigured to check port 8081 instead of the port nginx is listening on (80). | ✅ | no_action | no_action | ✅ |
| `stale_dependency` | dependency_connectivity | Readiness probe misconfiguration – the probe checks for a non‑existent file /tmp/ready, causing the pod to sta… | ❌ | restart_deployment / delete_pod | restart_deployment | ✅ |
| `svc_port_mismatch` | dependency_connectivity | The inventory service is unreachable from the storefront pod – likely the service has no endpoints or the inve… | ✅ | no_action | no_action | ✅ |

## Unknown resources referenced in kubectl commands (full context)

- `img_private_rollout`: name:debug-pod
- `pod_kill_single`: name:kc-eval-30/search-5d8cb9fc88-4lh62

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 3908s and retried 2 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
