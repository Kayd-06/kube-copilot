# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-19T12:23:07+00:00 → 2026-11-19T12:54:21+00:00 (UTC) on 17 frozen incidents from `ml/data/llm_eval/heldout_v3/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 14/17 = 82.4% (95% CI (59.0, 93.8)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.17s / 1.97s |
| Mean confidence when right / wrong | 0.929 / 0.917 |
| Remediation (first proposed action) correct | 16/17 = 94.1% (95% CI (73.0, 99.0)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/6 |
| Propose latency p50 / p95 | 0.72s / 1.14s |
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
| diagnose/full | 17 | 0 | 82.4% | 1.17s | 1.97s | 51207 | 10413 | 0.007 |
| propose/full | 17 | 0 | 94.1% | 0.72s | 1.14s | 34066 | 4845 | 0.004 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 1/1 | 1/1 |
| bad_config_value | 2/2 | 2/2 |
| cpu_throttling | 1/1 | 1/1 |
| dependency_connectivity | 2/2 | 2/2 |
| healthy | 1/2 | 2/2 |
| image_pull | 0/1 | 0/1 |
| missing_config | 2/3 | 3/3 |
| oom_killed | 1/1 | 1/1 |
| pod_disruption | 1/1 | 1/1 |
| probe_failure | 1/1 | 1/1 |
| unschedulable | 2/2 | 2/2 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 3/5 | 4/5 |
| hard | 3/4 | 4/4 |
| medium | 8/8 | 8/8 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_cm_key_first` | missing_config | The ConfigMap kc-ho-11/gateway-limits does not contain the required key RATE_LIMIT, preventing the pod from st… | ❌ | no_action | no_action | ✅ |
| `ho_cpu_limit_rollout` | cpu_throttling | CPU limit set too low, causing constant throttling of the encoder container. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_crash_flag_rollout` | app_crash | The pod spec passes the flag "--worker-threads" to the router binary, which does not recognize it, causing the… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_dns_proxy_first` | dependency_connectivity | Missing or misconfigured dependency service ledger-db-proxy referenced by DB_PROXY environment variable. | ✅ | no_action | no_action | ✅ |
| `ho_env_dburl_rollout` | bad_config_value | Malformed DATABASE_URL env var (missing scheme separator, e.g. "postgres//app@db:5432/accounts" instead of "po… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_healthy_mailer` | healthy | None – the pod is healthy and functioning correctly. | ✅ | no_action | no_action | ✅ |
| `ho_img_quay_rollout` | image_pull | DNS resolution failure for quay.io on the node kube-copilot-control-plane. | ❌ | rollback_deployment | no_action | ❌ |
| `ho_mem_request_rollout` | unschedulable | The pod’s memory request exceeds the allocatable memory on all available nodes. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_oom_render_first` | oom_killed | The container exceeded its memory limit (48Mi) while rendering, causing the kubelet to kill it. | ✅ | no_action | no_action | ✅ |
| `ho_pod_kill_two` | pod_disruption | External disruption caused the pod to be killed and replaced by the ReplicaSet; the current pod is healthy but… | ✅ | no_action | no_action | ✅ |
| `ho_readiness_http_rollout` | probe_failure | Readiness probe is misconfigured or the expected /ready endpoint does not exist in the nginx container. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_secret_volume_rollout` | missing_config | Missing secret "auth-tls" referenced as a volume in the pod spec. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_trap_configmap_reload` | bad_config_value | Invalid value for max_discount_pct in the engine.properties config file (set to "thirty" instead of an integer… | ✅ | no_action | no_action | ✅ |
| `ho_trap_dependency_down` | dependency_connectivity | Dependency ledger service is unavailable (0/0 ready pods). | ✅ | no_action | no_action | ✅ |
| `ho_trap_healthy_retry_logs` | healthy | Intermittent failures in the external partner‑api service causing transient timeouts/503s that the application… | ❌ | no_action | no_action | ✅ |
| `ho_trap_node_cordoned` | unschedulable | The sole node in the cluster is cordoned (unschedulable) or has a taint that the pod does not tolerate, preven… | ✅ | no_action | no_action | ✅ |
| `ho_trap_secret_deleted` | missing_config | The referenced secret "smtp-credentials" does not exist in the namespace kc-ho-16, preventing the pod from sta… | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 1146s and retried 1 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
