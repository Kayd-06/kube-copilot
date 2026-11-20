# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-19T12:54:22+00:00 → 2026-11-19T13:20:36+00:00 (UTC) on 38 frozen incidents from `ml/data/llm_eval/cases_v3/`. Status: **partial**.

> Stopped early: daily quota exhausted after 25 of 76 calls: LLM request failed: Error code: 429 - Rate limit reached for model `openai/gpt-oss-20b` in organization `org_01m5m2wscaegns4ra3dyn7txqr` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 197634, Requested 3575. Please try again in 8m42.288s. Need more tokens? Upgrade to Dev Tier t

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 20/25 = 80.0% (95% CI (60.9, 91.1)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.12s / 2.1s |
| Mean confidence when right / wrong | 0.92 / 0.95 |
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
| diagnose/full | 25 | 0 | 80.0% | 1.12s | 2.1s | 77869 | 16940 | 0.0109 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 3/4 | 0/0 |
| bad_config_value | 3/3 | 0/0 |
| cpu_throttling | 1/1 | 0/0 |
| dependency_connectivity | 1/1 | 0/0 |
| healthy | 2/4 | 0/0 |
| image_pull | 2/3 | 0/0 |
| missing_config | 4/5 | 0/0 |
| oom_killed | 2/2 | 0/0 |
| probe_failure | 2/2 | 0/0 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 11/12 | 0/0 |
| hard | 6/7 | 0/0 |
| medium | 3/6 | 0/0 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `cm_deleted_after_rollout` | missing_config | Missing ConfigMap "pricing-config" referenced by the pod. | ✅ | no_action |  | ❌ |
| `cm_edited_after_rollout` | bad_config_value | The container is started with an invalid log level argument ("verbose"), leading to a fatal error and exit cod… | ✅ | no_action |  | ❌ |
| `config_loglevel_first` | bad_config_value | The notifier container is started with an invalid log level ("verbose"), which the application rejects and exi… | ✅ | no_action |  | ❌ |
| `config_port_rollout` | bad_config_value | Misconfigured PORT environment variable (set to "eighty" instead of a valid integer). | ✅ | rollback_deployment |  | ❌ |
| `cpu_throttle_first` | cpu_throttling | CPU limit too low, leading to throttling of the container. | ✅ | no_action / scale_deployment |  | ❌ |
| `crash_bad_command_rollout` | app_crash | The container image or command is misconfigured – the image busybox:1.36 does not contain the executable /app/… | ✅ | rollback_deployment |  | ❌ |
| `crash_panic_rollout` | app_crash | Application code bug causing a nil pointer dereference during startup (Router.Register called on a nil instanc… | ✅ | rollback_deployment |  | ❌ |
| `crash_traceback_first` | app_crash | The worker application lacks a handler for the job type 'invoice.v2', causing a KeyError and process terminati… | ❌ | no_action |  | ❌ |
| `dns_missing_service` | dependency_connectivity | Missing or mis‑configured upstream Service "pricing-api" in namespace kc-eval-28, leading to DNS resolution fa… | ✅ | no_action |  | ❌ |
| `healthy_after_rollout` | healthy | No operational issue – the pod is healthy; the anomaly flag is a false positive. | ✅ | no_action |  | ❌ |
| `healthy_job_completed` | healthy | None – the pod finished as intended. | ❌ | no_action |  | ❌ |
| `healthy_noisy_logs` | healthy | Intermittent upstream service latency or timeout causing the application to retry, not a pod failure. | ❌ | no_action |  | ❌ |
| `healthy_web` | healthy | None – the pod is healthy; the anomaly score is a false positive. | ✅ | no_action |  | ❌ |
| `img_private_rollout` | image_pull | DNS resolution failure on the node (kube-copilot-control-plane) for ghcr.io, likely due to a misbehaving or un… | ❌ | rollback_deployment |  | ❌ |
| `img_registry_first` | image_pull | DNS resolution failure for the image registry host (registry.example.invalid) on the node, causing the kubelet… | ✅ | no_action |  | ❌ |
| `img_tag_rollout` | image_pull | Typo in the image tag – the pod is requesting a non‑existent image "nginx:1.27-alpine-typo" which cannot be re… | ✅ | rollback_deployment |  | ❌ |
| `init_migration_first` | app_crash | The init container "run-migrations" exits with an error (likely a script or configuration issue) and never suc… | ✅ | no_action |  | ❌ |
| `liveness_path_rollout` | probe_failure | Liveness probe is incorrectly configured to hit /healthz, which is missing from the nginx image, causing repea… | ✅ | rollback_deployment |  | ❌ |
| `liveness_slow_start_first` | probe_failure | The liveness probe is too aggressive (short initialDelaySeconds/failureThreshold) and is hitting the container… | ✅ | no_action |  | ❌ |
| `missing_cm_envfrom_rollout` | missing_config | ConfigMap "cart-feature-flags" referenced in the pod spec does not exist in namespace kc-eval-10. | ✅ | rollback_deployment |  | ❌ |
| `missing_cm_volume_first` | missing_config | Missing ConfigMap "search-synonyms" referenced as a volume in the pod spec. | ✅ | no_action |  | ❌ |
| `missing_secret_first` | missing_config | The referenced secret "orders-db-credentials" does not exist in the kc-eval-11 namespace, causing a CreateCont… | ✅ | no_action |  | ❌ |
| `missing_secret_key_rollout` | missing_config | The Secret `kc-eval-13/ledger-api` does not contain the required key `api_token`, causing the container config… | ❌ | rollback_deployment |  | ❌ |
| `oom_first` | oom_killed | The pod’s memory limit is too low for the amount of data the application loads at startup. | ✅ | no_action |  | ❌ |
| `oom_leak_first` | oom_killed | The container exceeded its memory limit (or the node ran out of memory), leading to an OOM kill. | ✅ | no_action |  | ❌ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 1207s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
