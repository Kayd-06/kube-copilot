# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-13T18:01:14+00:00 → 2026-11-13T18:39:12+00:00 (UTC) on 38 frozen incidents from `ml/data/llm_eval/cases/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 26/38 = 68.4% (95% CI (52.5, 80.9)) |
| JSON/schema valid | 97.4% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.77s / 3.42s |
| Mean confidence when right / wrong | 0.898 / 0.837 |
| Remediation (first proposed action) correct | 30/38 = 78.9% (95% CI (63.7, 88.9)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/4 |
| Propose latency p50 / p95 | 0.96s / 2.36s |
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
| diagnose/full | 38 | 1 | 68.4% | 1.77s | 3.42s | 82017 | 23195 | 0.0262 |
| propose/full | 38 | 0 | 78.9% | 0.96s | 2.36s | 62461 | 10092 | 0.0154 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 3/4 | 4/4 |
| bad_config_value | 1/3 | 2/3 |
| cpu_throttling | 0/1 | 1/1 |
| dependency_connectivity | 2/3 | 2/3 |
| healthy | 2/4 | 4/4 |
| image_pull | 3/3 | 3/3 |
| missing_config | 4/5 | 3/5 |
| oom_killed | 3/3 | 2/3 |
| pod_disruption | 0/3 | 3/3 |
| probe_failure | 3/3 | 2/4 |
| pvc_pending | 2/2 | 2/2 |
| unschedulable | 3/3 | 2/3 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 14/16 | 12/17 |
| hard | 6/11 | 10/11 |
| medium | 6/10 | 8/10 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `cm_deleted_after_rollout` | missing_config | Missing or miss‑named ConfigMap "pricing-config" in the kc-eval-32 namespace that the pod’s volume/env referen… | ✅ | no_action | no_action | ✅ |
| `cm_edited_after_rollout` | bad_config_value | The container’s startup command or environment sets a log level of "verbose", which the application does not r… | ❌ | no_action | no_action | ✅ |
| `config_loglevel_first` | bad_config_value | The container’s entrypoint or configuration passes an invalid log level ("verbose") that the application does … | ✅ | no_action | no_action | ✅ |
| `config_port_rollout` | bad_config_value | The PORT environment variable is set to the non‑numeric string "eighty" instead of a valid integer, causing th… | ❌ | rollback_deployment | no_action | ❌ |
| `cpu_throttle_first` | cpu_throttling | No actual failure; the pod is healthy. The anomaly flag is a false positive caused by unusually low resource c… | ❌ | no_action / scale_deployment | no_action | ✅ |
| `crash_bad_command_rollout` | app_crash | Incorrect container image or command configuration – the pod spec points to "/app/server" which does not exist… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_panic_rollout` | app_crash | Application bug – the Router instance is nil when Register is called (likely due to missing or malformed confi… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `crash_traceback_first` | app_crash | Application code error – the job type 'invoice.v2' is not present in the HANDLERS dictionary, causing an unhan… | ❌ | no_action | no_action | ✅ |
| `dns_missing_service` | dependency_connectivity | DNS resolution / connectivity failure to the upstream service `pricing-api` (service missing, mis‑named, or bl… | ✅ | no_action | no_action | ✅ |
| `healthy_after_rollout` | healthy | No memory limits are set (QoS Burstable) and the nginx process is exhibiting a rising memory footprint, which … | ❌ | no_action | no_action | ✅ |
| `healthy_job_completed` | healthy | There is no failure – the pod is a short‑lived job that finishes and terminates normally (exit code 0). | ✅ | no_action | no_action | ✅ |
| `healthy_noisy_logs` | healthy | Network or service outage for the upstream endpoint the sync container contacts (e.g., DNS mis‑resolution, Ser… | ❌ | no_action | no_action | ✅ |
| `healthy_web` | healthy | The anomaly is likely a false positive caused by a short observation window and low baseline usage; the pod it… | ✅ | no_action | no_action | ✅ |
| `img_private_rollout` | image_pull | DNS resolution failure for ghcr.io caused by a malfunctioning CoreDNS service or node DNS configuration, preve… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `img_registry_first` | image_pull | DNS resolution failure for the image registry host (registry.example.invalid) preventing the node from pulling… | ✅ | no_action | no_action | ✅ |
| `img_tag_rollout` | image_pull | DNS resolution failure for registry-1.docker.io from the node (CoreDNS or node /etc/resolv.conf misconfigurati… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `init_migration_first` | app_crash | Misconfigured container image/command: the run-migrations container is using a minimal busybox image that cann… | ✅ | no_action | no_action | ✅ |
| `liveness_path_rollout` | probe_failure | LLM request failed: Error code: 413 - Request too large for model `openai/gpt-oss-120b` in organization `org_0… | ❌ | rollback_deployment | no_action | ❌ |
| `liveness_slow_start_first` | probe_failure | Liveness probe is too aggressive – it checks the HTTP endpoint before the nginx process is listening, leading … | ✅ | no_action | no_action | ✅ |
| `missing_cm_envfrom_rollout` | missing_config | Missing ConfigMap cart-feature-flags (or typo in its name) referenced by the pod’s volume/env configuration. | ✅ | rollback_deployment | no_action | ❌ |
| `missing_cm_volume_first` | missing_config | Missing ConfigMap "search-synonyms" referenced in the pod’s volume definition. | ✅ | no_action | no_action | ✅ |
| `missing_secret_first` | missing_config | Missing Kubernetes Secret named "orders-db-credentials" referenced by the pod spec. | ✅ | no_action | no_action | ✅ |
| `missing_secret_key_rollout` | missing_config | The Secret kc‑eval‑13/ledger‑api exists but does not contain the required key api_token (or the key is misspel… | ❌ | rollback_deployment | no_action | ❌ |
| `oom_first` | oom_killed | Memory limit too low for the catalog loading step, causing the container to be OOM‑killed during startup. | ✅ | no_action | no_action | ✅ |
| `oom_leak_first` | oom_killed | A memory leak or insufficient memory limit causing the container to exceed its configured memory quota, leadin… | ✅ | no_action | no_action | ✅ |
| `oom_limit_rollout` | oom_killed | The container’s memory limit is too low for the workload (or the application has a memory leak), leading to OO… | ✅ | rollback_deployment | no_action | ❌ |
| `pending_antiaffinity_first` | unschedulable | A strict pod anti‑affinity rule (requiring the pod not to run on a node that already hosts a matching pod) can… | ✅ | no_action / scale_deployment | no_action | ✅ |
| `pending_cpu_first` | unschedulable | The pod's CPU request exceeds the amount of allocatable CPU on the only available node, causing a scheduling f… | ✅ | no_action | no_action | ✅ |
| `pending_nodeselector_rollout` | unschedulable | The pod’s nodeAffinity (or nodeSelector) constraints do not match any node’s labels/taints, preventing schedul… | ✅ | rollback_deployment | no_action | ❌ |
| `pod_kill_after_rollout` | pod_disruption | No failure – the pod is healthy and operating as expected. | ❌ | no_action | no_action | ✅ |
| `pod_kill_repeated` | pod_disruption | No actual failure; the observed pod churn is caused by the Deployment controller performing rapid rollouts (e.… | ❌ | no_action | no_action | ✅ |
| `pod_kill_single` | pod_disruption | A memory leak or abnormal memory growth in the nginx container, likely caused by the current configuration or … | ❌ | no_action | no_action | ✅ |
| `pvc_missing_first` | pvc_pending | The required PersistentVolumeClaim "exports-data" does not exist (or is in the wrong namespace), causing the p… | ✅ | no_action | no_action | ✅ |
| `pvc_storageclass_first` | pvc_pending | The PersistentVolumeClaim required by the pod has not bound to any PersistentVolume (no matching PV or dynamic… | ✅ | no_action | no_action | ✅ |
| `readiness_exec_rollout` | probe_failure | Readiness probe misconfiguration – the probe expects a file that the container never creates. | ✅ | rollback_deployment | no_action | ❌ |
| `readiness_port_first` | probe_failure | Readiness probe is misconfigured to target port 8081, which the nginx container does not expose. | ✅ | no_action | no_action | ✅ |
| `stale_dependency` | dependency_connectivity | Readiness probe misconfiguration – the probe expects a file (/tmp/ready) that the application does not create,… | ❌ | restart_deployment / delete_pod | no_action | ❌ |
| `svc_port_mismatch` | dependency_connectivity | The `inventory` service is unavailable or unreachable from the storefront pod (missing endpoints, down pods, o… | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 1824s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
