# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-13T18:01:14+00:00 → 2026-11-13T18:38:59+00:00 (UTC) on 38 frozen incidents from `ml/data/llm_eval/cases/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 27/38 = 71.1% (95% CI (55.2, 83.0)) |
| JSON/schema valid | 97.4% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.32s / 2.26s |
| Mean confidence when right / wrong | 0.899 / 0.81 |
| Remediation (first proposed action) correct | 29/38 = 76.3% (95% CI (60.8, 87.0)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/4 |
| Propose latency p50 / p95 | 0.85s / 1.49s |
| RAG: correct runbook in top-5 | 86.2% of 29 cases (hit@1 69.0%, MRR 0.738) |

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 55.3% |
| Remediation: always `no_action` | 68.4% |
| Remediation: rollback if a previous revision exists, else `no_action` | 86.8% |

## All runs

| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| diagnose/full | 38 | 1 | 71.1% | 1.32s | 2.26s | 82017 | 26649 | 0.0141 |
| propose/full | 38 | 1 | 76.3% | 0.85s | 1.49s | 60521 | 13484 | 0.0086 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 3/4 | 4/4 |
| bad_config_value | 2/3 | 2/3 |
| cpu_throttling | 0/1 | 1/1 |
| dependency_connectivity | 3/3 | 2/3 |
| healthy | 1/4 | 3/3 |
| image_pull | 2/3 | 3/3 |
| missing_config | 5/5 | 3/5 |
| oom_killed | 3/3 | 2/3 |
| pod_disruption | 0/3 | 3/3 |
| probe_failure | 3/3 | 2/4 |
| pvc_pending | 2/2 | 2/2 |
| unschedulable | 3/3 | 2/3 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 13/16 | 11/16 |
| hard | 8/11 | 10/11 |
| medium | 6/10 | 8/10 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `cm_deleted_after_rollout` | missing_config | Missing ConfigMap "pricing-config" referenced in the pod spec. | ✅ | no_action | no_action | ✅ |
| `cm_edited_after_rollout` | bad_config_value | The container is launched with an invalid log level ("verbose") that the application does not recognize, causi… | ✅ | no_action | no_action | ✅ |
| `config_loglevel_first` | bad_config_value | An invalid log level ("verbose") is passed to the notifier binary, causing it to exit immediately. | ✅ | no_action | no_action | ✅ |
| `config_port_rollout` | bad_config_value | The PORT environment variable is set to the string "eighty" instead of a numeric value, causing the applicatio… | ❌ | rollback_deployment | no_action | ❌ |
| `cpu_throttle_first` | cpu_throttling | Resource limits are set too high, causing the anomaly detector to flag low usage even though the pod is health… | ❌ | no_action / scale_deployment | no_action | ✅ |
| `crash_bad_command_rollout` | app_crash | The pod spec references an image (busybox:1.36) that does not contain the binary /app/server, or the command/w… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_panic_rollout` | app_crash | Application bug (nil pointer dereference in Router.Register) in the gateway binary. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_traceback_first` | app_crash | Missing or misconfigured handler for job type 'invoice.v2' in the worker application. | ❌ | no_action | no_action | ✅ |
| `dns_missing_service` | dependency_connectivity | DNS resolution failure for the service "pricing-api" (service missing or mis‑configured in the namespace). | ✅ | no_action | no_action | ✅ |
| `healthy_after_rollout` | healthy | Memory usage is trending upward (high slope) on a control‑plane node without resource limits, indicating a pos… | ❌ | no_action | no_action | ✅ |
| `healthy_job_completed` | healthy | The pod is a Job that finished its task; the container exited normally, so the pod is in the Succeeded state. | ✅ | no_action | no_action | ✅ |
| `healthy_noisy_logs` | healthy | The upstream service that the sync worker contacts is unreachable or unresponsive (network, DNS, service outag… | ❌ | no_action | no_action | ✅ |
| `healthy_web` | healthy | Anomaly detector false positive caused by low overall resource usage (CPU, memory, network) and a transient sp… | ❌ | no_action | LLM tool call failed: Error code: 400 -  | ❌ |
| `img_private_rollout` | image_pull | DNS resolution failure for ghcr.io on the node (likely CoreDNS or node DNS configuration issue). | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `img_registry_first` | image_pull | DNS resolution failure for the image registry host (registry.example.invalid) on the node’s DNS server. | ✅ | no_action | no_action | ✅ |
| `img_tag_rollout` | image_pull | DNS resolution failure to Docker Hub (registry-1.docker.io) combined with an invalid image tag. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `init_migration_first` | app_crash | The init container "run-migrations" is failing to run (likely due to a missing or incorrect command/script) an… | ✅ | no_action | no_action | ✅ |
| `liveness_path_rollout` | probe_failure | LLM request failed: Error code: 413 - Request too large for model `openai/gpt-oss-20b` in organization `org_01… | ❌ | rollback_deployment | no_action | ❌ |
| `liveness_slow_start_first` | probe_failure | The liveness probe is too aggressive (short initialDelaySeconds/failureThreshold) for a container that needs ~… | ✅ | no_action | no_action | ✅ |
| `missing_cm_envfrom_rollout` | missing_config | Missing ConfigMap "cart-feature-flags" referenced in the pod spec. | ✅ | rollback_deployment | no_action | ❌ |
| `missing_cm_volume_first` | missing_config | Missing ConfigMap "search-synonyms" referenced as a volume in the pod spec. | ✅ | no_action | no_action | ✅ |
| `missing_secret_first` | missing_config | Missing or mis‑named secret "orders-db-credentials" in namespace kc-eval-11. | ✅ | no_action | no_action | ✅ |
| `missing_secret_key_rollout` | missing_config | Missing or incomplete Secret "ledger-api" – the key "api_token" does not exist in the Secret referenced by the… | ✅ | rollback_deployment | no_action | ❌ |
| `oom_first` | oom_killed | The pod’s memory limit is insufficient for the product catalog load, causing the container to be OOMKilled. | ✅ | no_action | no_action | ✅ |
| `oom_leak_first` | oom_killed | The container exceeded its memory limit (or the node ran out of memory) due to a memory leak or insufficient m… | ✅ | no_action | no_action | ✅ |
| `oom_limit_rollout` | oom_killed | The container’s memory usage exceeds the limit set in its pod spec, leading to OOM kills by the kubelet. | ✅ | rollback_deployment | no_action | ❌ |
| `pending_antiaffinity_first` | unschedulable | The pod’s requiredDuringScheduling anti‑affinity rule conflicts with an existing pod on the sole node, prevent… | ✅ | no_action / scale_deployment | no_action | ✅ |
| `pending_cpu_first` | unschedulable | CPU resource request exceeds the allocatable CPU on the sole available node. | ✅ | no_action | no_action | ✅ |
| `pending_nodeselector_rollout` | unschedulable | Node affinity/selector mismatch – the pod’s nodeSelector or nodeAffinity rules do not match any node labels. | ✅ | rollback_deployment | no_action | ❌ |
| `pod_kill_after_rollout` | pod_disruption | None – the pod is healthy and functioning as expected. | ❌ | no_action | no_action | ✅ |
| `pod_kill_repeated` | pod_disruption | None – the pod is healthy and functioning as expected. | ❌ | no_action | no_action | ✅ |
| `pod_kill_single` | pod_disruption | Early‑warning memory anomaly – the pod’s memory consumption is rising rapidly (high slope) even though current… | ❌ | no_action | no_action | ✅ |
| `pvc_missing_first` | pvc_pending | Missing or mis‑named PersistentVolumeClaim "exports-data" referenced in the pod spec. | ✅ | no_action | no_action | ✅ |
| `pvc_storageclass_first` | pvc_pending | The PersistentVolumeClaim used by the pod has not been bound to a PersistentVolume, preventing the pod from be… | ✅ | no_action | no_action | ✅ |
| `readiness_exec_rollout` | probe_failure | Readiness probe misconfiguration – the probe checks for a non‑existent file /tmp/ready instead of a health end… | ✅ | rollback_deployment | no_action | ❌ |
| `readiness_port_first` | probe_failure | Readiness probe configured to check port 8081, but nginx is listening on port 80, causing the probe to fail an… | ✅ | no_action | no_action | ✅ |
| `stale_dependency` | dependency_connectivity | The application cannot reach its upstream service 'deps-api', preventing it from creating the '/tmp/ready' fil… | ✅ | restart_deployment / delete_pod | no_action | ❌ |
| `svc_port_mismatch` | dependency_connectivity | The inventory service is either not running, not exposing port 80, or not reachable from the storefront pod (m… | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 1831s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
