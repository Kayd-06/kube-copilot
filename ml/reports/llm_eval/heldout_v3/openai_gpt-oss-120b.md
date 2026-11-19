# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-19T12:23:06+00:00 → 2026-11-19T12:55:27+00:00 (UTC) on 17 frozen incidents from `ml/data/llm_eval/heldout_v3/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 17/17 = 100.0% (95% CI (81.6, 100.0)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.75s / 2.07s |
| Mean confidence when right / wrong | 0.923 / n/a |
| Remediation (first proposed action) correct | 16/17 = 94.1% (95% CI (73.0, 99.0)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/6 |
| Propose latency p50 / p95 | 0.9s / 1.56s |
| RAG: correct runbook in top-5 | 92.3% of 13 cases (hit@1 76.9%, MRR 0.821) |

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 52.9% |
| Remediation: always `no_action` | 58.8% |
| Remediation: rollback if a previous revision exists, else `no_action` | 70.6% |

## All runs

| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| diagnose/full | 17 | 0 | 100.0% | 1.75s | 2.07s | 51207 | 9612 | 0.0134 |
| propose/full | 17 | 0 | 94.1% | 0.9s | 1.56s | 34066 | 4714 | 0.0079 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 1/1 | 1/1 |
| bad_config_value | 2/2 | 2/2 |
| cpu_throttling | 1/1 | 1/1 |
| dependency_connectivity | 2/2 | 2/2 |
| healthy | 2/2 | 2/2 |
| image_pull | 1/1 | 1/1 |
| missing_config | 3/3 | 2/3 |
| oom_killed | 1/1 | 1/1 |
| pod_disruption | 1/1 | 1/1 |
| probe_failure | 1/1 | 1/1 |
| unschedulable | 2/2 | 2/2 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 5/5 | 5/5 |
| hard | 4/4 | 4/4 |
| medium | 8/8 | 7/8 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_cm_key_first` | missing_config | Missing RATE_LIMIT key in the referenced ConfigMap (kc-ho-11/gateway-limits). | ✅ | no_action | no_action | ✅ |
| `ho_cpu_limit_rollout` | cpu_throttling | CPU limit (15m) is too low for the encoder’s workload, leading to continuous CPU throttling and degraded perfo… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_crash_flag_rollout` | app_crash | Invalid command/args in the pod spec – the container is started with an unsupported flag (--worker-threads), l… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_dns_proxy_first` | dependency_connectivity | Missing or misnamed Service ledger-db-proxy in namespace kc-ho-12, causing the settlement container to fail at… | ✅ | no_action | no_action | ✅ |
| `ho_env_dburl_rollout` | bad_config_value | Invalid DATABASE_URL environment variable causing the container to exit on startup | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_healthy_mailer` | healthy | False‑positive anomaly detection; the pod is healthy. | ✅ | no_action | no_action | ✅ |
| `ho_img_quay_rollout` | image_pull | DNS resolution failure for quay.io from the node (cluster DNS or network issue), preventing the image pull. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_mem_request_rollout` | unschedulable | The pod’s memory request is larger than the allocatable memory on the sole node, leading to an Unschedulable e… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_oom_render_first` | oom_killed | Memory limit too low – the container runs out of memory and is killed (OOMKilled), triggering CrashLoopBackOff… | ✅ | no_action | no_action | ✅ |
| `ho_pod_kill_two` | pod_disruption | A recent pod replacement triggered by a Deployment rollout (e.g., image/config change or manual rollout) cause… | ✅ | no_action | no_action | ✅ |
| `ho_readiness_http_rollout` | probe_failure | Readiness probe misconfiguration – the probe checks /ready which does not exist in the nginx container image. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_secret_volume_rollout` | missing_config | Missing secret "auth-tls" required for the pod’s volume mount (FailedMount event). | ✅ | rollback_deployment | no_action | ❌ |
| `ho_trap_configmap_reload` | bad_config_value | Invalid value for `max_discount_pct` in the quote‑engine configuration (engine.properties) – a non‑integer str… | ✅ | no_action | no_action | ✅ |
| `ho_trap_dependency_down` | dependency_connectivity | The checkout container exits because its required dependency (the ledger service) is unavailable, causing it t… | ✅ | no_action | no_action | ✅ |
| `ho_trap_healthy_retry_logs` | healthy | Intermittent failures of the external partner‑api service that the pod contacts; the pod itself is healthy and… | ✅ | no_action | no_action | ✅ |
| `ho_trap_node_cordoned` | unschedulable | The sole node is marked unschedulable (e.g., cordoned, NotReady, or has a taint that the pod does not tolerate… | ✅ | no_action | no_action | ✅ |
| `ho_trap_secret_deleted` | missing_config | Missing secret "smtp-credentials" in namespace kc-ho-16 referenced by the pod spec. | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 1200s and retried 1 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
