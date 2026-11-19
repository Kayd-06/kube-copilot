# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-19T12:55:28+00:00 → 2026-11-19T13:27:42+00:00 (UTC) on 38 frozen incidents from `ml/data/llm_eval/cases_v3/`. Status: **partial**.

> Stopped early: daily quota exhausted after 30 of 76 calls: LLM request failed: Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01m5m2wscaegns4ra3dyn7txqr` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 197822, Requested 4658. Please try again in 17m51.359999999s. Need more tokens? Upgrade to De

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 29/30 = 96.7% (95% CI (83.3, 99.4)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.94s / 3.88s |
| Mean confidence when right / wrong | 0.913 / 0.73 |
| RAG: correct runbook in top-5 | 86.2% of 29 cases (hit@1 72.4%, MRR 0.758) |

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 55.3% |
| Remediation: always `no_action` | 68.4% |
| Remediation: rollback if a previous revision exists, else `no_action` | 86.8% |

## All runs

| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| diagnose/full | 30 | 0 | 96.7% | 1.94s | 3.88s | 91674 | 18130 | 0.0246 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 4/4 | 0/0 |
| bad_config_value | 3/3 | 0/0 |
| cpu_throttling | 1/1 | 0/0 |
| dependency_connectivity | 1/1 | 0/0 |
| healthy | 3/4 | 0/0 |
| image_pull | 3/3 | 0/0 |
| missing_config | 5/5 | 0/0 |
| oom_killed | 3/3 | 0/0 |
| pod_disruption | 1/1 | 0/0 |
| probe_failure | 2/2 | 0/0 |
| unschedulable | 3/3 | 0/0 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 15/15 | 0/0 |
| hard | 8/9 | 0/0 |
| medium | 6/6 | 0/0 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `cm_deleted_after_rollout` | missing_config | Missing ConfigMap "pricing-config" referenced by the pod spec, causing kubelet to reject container creation. | ✅ | no_action |  | ❌ |
| `cm_edited_after_rollout` | bad_config_value | Invalid log‑level argument (or env var) passed to the container’s command, causing the process to exit with co… | ✅ | no_action |  | ❌ |
| `config_loglevel_first` | bad_config_value | The container’s start command (or env var) supplies an invalid log level "verbose", which the application does… | ✅ | no_action |  | ❌ |
| `config_port_rollout` | bad_config_value | Invalid value for the PORT environment variable (non‑integer) causing the container to exit on start‑up. | ✅ | rollback_deployment |  | ❌ |
| `cpu_throttle_first` | cpu_throttling | The container’s CPU limit (20 m) is too low for the workload’s bursty demand, causing extreme CPU throttling a… | ✅ | no_action / scale_deployment |  | ❌ |
| `crash_bad_command_rollout` | app_crash | Container command points to a non‑existent binary (/app/server) in the busybox:1.36 image, resulting in an OCI… | ✅ | rollback_deployment |  | ❌ |
| `crash_panic_rollout` | app_crash | Application code panics at startup (nil pointer in Router.Register), causing the container to exit with code 2… | ✅ | rollback_deployment |  | ❌ |
| `crash_traceback_first` | app_crash | Application code raises an unhandled KeyError because the job type 'invoice.v2' is not present in the HANDLERS… | ✅ | no_action |  | ❌ |
| `dns_missing_service` | dependency_connectivity | Missing in‑cluster Service named pricing‑api (dependency) causing DNS resolution failure and container exit. | ✅ | no_action |  | ❌ |
| `healthy_after_rollout` | healthy | False‑positive anomaly detection; the pod is operating normally. | ✅ | no_action |  | ❌ |
| `healthy_job_completed` | healthy | No failure – the pod finished its intended one‑shot workload as designed. | ✅ | no_action |  | ❌ |
| `healthy_noisy_logs` | healthy | Intermittent timeouts when contacting an upstream dependency, causing the pod to emit WARN messages and low ca… | ❌ | no_action |  | ❌ |
| `healthy_web` | healthy | False‑positive anomaly detection; the pod is healthy. | ✅ | no_action |  | ❌ |
| `img_private_rollout` | image_pull | DNS resolution failure to ghcr.io from the node (CoreDNS or node /etc/resolv.conf misconfiguration), preventin… | ✅ | rollback_deployment |  | ❌ |
| `img_registry_first` | image_pull | DNS resolution failure for the image registry (registry.example.invalid) prevents the node from reaching the r… | ✅ | no_action |  | ❌ |
| `img_tag_rollout` | image_pull | DNS resolution failure to Docker Hub from the node (cluster DNS issue), preventing the image pull; the image t… | ✅ | rollback_deployment |  | ❌ |
| `init_migration_first` | app_crash | The run-migrations container crashes on start (non‑zero exit code), likely due to a runtime error such as miss… | ✅ | no_action |  | ❌ |
| `liveness_path_rollout` | probe_failure | Liveness probe misconfiguration – the probe checks /healthz which does not exist in the nginx:1.27‑alpine imag… | ✅ | rollback_deployment |  | ❌ |
| `liveness_slow_start_first` | probe_failure | Liveness probe is too aggressive for the container's startup time, causing the kubelet to kill the process bef… | ✅ | no_action |  | ❌ |
| `missing_cm_envfrom_rollout` | missing_config | Missing ConfigMap "cart-feature-flags" referenced by the pod spec (or wrong namespace/name). | ✅ | rollback_deployment |  | ❌ |
| `missing_cm_volume_first` | missing_config | Missing ConfigMap "search-synonyms" referenced by the pod’s volume, causing a FailedMount event and preventing… | ✅ | no_action |  | ❌ |
| `missing_secret_first` | missing_config | Missing secret "orders-db-credentials" referenced by the pod spec. | ✅ | no_action |  | ❌ |
| `missing_secret_key_rollout` | missing_config | The Secret "ledger-api" in namespace "kc-eval-13" is missing the required key "api_token" (or the secret itsel… | ✅ | rollback_deployment |  | ❌ |
| `oom_first` | oom_killed | The container exceeds its configured memory limit, triggering an OOM kill and subsequent CrashLoopBackOff. | ✅ | no_action |  | ❌ |
| `oom_leak_first` | oom_killed | The container exceeded its memory limit (or was killed by node memory pressure), triggering an OOM kill. | ✅ | no_action |  | ❌ |
| `oom_limit_rollout` | oom_killed | The container exceeds its 64Mi memory limit, triggering OOMKilled and subsequent BackOff restarts. | ✅ | rollback_deployment |  | ❌ |
| `pending_antiaffinity_first` | unschedulable | Strict pod anti‑affinity rule that excludes the sole available node, making the pod unschedulable. | ✅ | no_action / scale_deployment |  | ❌ |
| `pending_cpu_first` | unschedulable | The pod’s CPU request (or limit) is higher than the allocatable CPU on every node, leaving no node with enough… | ✅ | no_action |  | ❌ |
| `pending_nodeselector_rollout` | unschedulable | Node affinity/selector defined in the pod spec does not match any node’s labels, making the pod unschedulable. | ✅ | rollback_deployment |  | ❌ |
| `pod_kill_after_rollout` | pod_disruption | An external disruption (e.g., manual delete, node drain, or controller‑initiated rollout) killed the previous … | ✅ | no_action |  | ❌ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 1387s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
