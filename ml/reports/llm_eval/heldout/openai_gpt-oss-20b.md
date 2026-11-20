# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-17T20:12:31+00:00 → 2026-11-17T20:28:24+00:00 (UTC) on 12 frozen incidents from `ml/data/llm_eval/heldout/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 9/12 = 75.0% (95% CI (46.8, 91.1)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.14s / 1.85s |
| Mean confidence when right / wrong | 0.922 / 0.933 |
| Remediation (first proposed action) correct | 11/12 = 91.7% (95% CI (64.6, 98.5)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/6 |
| Propose latency p50 / p95 | 0.73s / 2.97s |
| RAG: correct runbook in top-5 | 88.9% of 9 cases (hit@1 77.8%, MRR 0.833) |

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 50.0% |
| Remediation: always `no_action` | 41.7% |
| Remediation: rollback if a previous revision exists, else `no_action` | 100.0% |

## All runs

| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| diagnose/full | 12 | 0 | 75.0% | 1.14s | 1.85s | 30935 | 7050 | 0.0044 |
| propose/full | 12 | 0 | 91.7% | 0.73s | 2.97s | 21318 | 4610 | 0.003 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 1/1 | 1/1 |
| bad_config_value | 0/1 | 1/1 |
| cpu_throttling | 1/1 | 0/1 |
| dependency_connectivity | 1/1 | 1/1 |
| healthy | 0/1 | 1/1 |
| image_pull | 0/1 | 1/1 |
| missing_config | 2/2 | 2/2 |
| oom_killed | 1/1 | 1/1 |
| pod_disruption | 1/1 | 1/1 |
| probe_failure | 1/1 | 1/1 |
| unschedulable | 1/1 | 1/1 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 3/5 | 5/5 |
| hard | 1/1 | 0/1 |
| medium | 5/6 | 6/6 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_cm_key_first` | missing_config | Missing key RATE_LIMIT in ConfigMap kc-ho-11/gateway-limits referenced by the pod spec. | ✅ | no_action | no_action | ✅ |
| `ho_cpu_limit_rollout` | cpu_throttling | CPU throttling due to an overly restrictive CPU limit (15m) for the workload. | ✅ | rollback_deployment | no_action | ❌ |
| `ho_crash_flag_rollout` | app_crash | The container is started with the flag --worker-threads, which the binary does not recognize, causing it to te… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_dns_proxy_first` | dependency_connectivity | The pod’s dependency service "ledger-db-proxy" is missing or unreachable (DNS resolution failure). | ✅ | no_action | no_action | ✅ |
| `ho_env_dburl_rollout` | bad_config_value | Misconfigured DATABASE_URL (missing colon after postgres, missing user) causing the application to fail on sta… | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `ho_healthy_mailer` | healthy | Anomaly detector false positive due to low resource usage and normal logs. | ❌ | no_action | no_action | ✅ |
| `ho_img_quay_rollout` | image_pull | Cluster DNS resolution failure for quay.io, likely due to a misbehaving CoreDNS pod or network issue. | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `ho_mem_request_rollout` | unschedulable | Memory request for the pod exceeds the allocatable memory on all nodes in the cluster. | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_oom_render_first` | oom_killed | The memory limit for the pdf-render container is too low for the workload’s actual memory usage. | ✅ | no_action | no_action | ✅ |
| `ho_pod_kill_two` | pod_disruption | External pod disruption (node drain or manual delete) caused the pod to be killed and replaced by the ReplicaS… | ✅ | no_action | no_action | ✅ |
| `ho_readiness_http_rollout` | probe_failure | Readiness probe path is incorrect – the probe requests /ready, but the nginx image does not provide that endpo… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_secret_volume_rollout` | missing_config | Missing or mis‑named secret "auth-tls" referenced as a volume. | ✅ | rollback_deployment | rollback_deployment | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 594s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
