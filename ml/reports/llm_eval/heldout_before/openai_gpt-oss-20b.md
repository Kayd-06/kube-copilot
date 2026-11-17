# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-17T20:28:25+00:00 → 2026-11-17T20:46:34+00:00 (UTC) on 12 frozen incidents from `ml/data/llm_eval/cases/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 8/12 = 66.7% (95% CI (39.1, 86.2)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.14s / 1.96s |
| Mean confidence when right / wrong | 0.919 / 0.812 |
| Remediation (first proposed action) correct | 7/12 = 58.3% (95% CI (32.0, 80.7)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/2 |
| Propose latency p50 / p95 | 0.77s / 0.94s |
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
| diagnose/full | 12 | 0 | 66.7% | 1.14s | 1.96s | 30995 | 7046 | 0.0044 |
| propose/full | 12 | 0 | 58.3% | 0.77s | 0.94s | 21330 | 3909 | 0.0028 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 0/1 | 1/1 |
| bad_config_value | 1/1 | 0/1 |
| cpu_throttling | 0/1 | 0/1 |
| dependency_connectivity | 1/1 | 1/1 |
| healthy | 1/1 | 1/1 |
| image_pull | 0/1 | 1/1 |
| missing_config | 2/2 | 1/2 |
| oom_killed | 1/1 | 1/1 |
| pod_disruption | 0/1 | 1/1 |
| probe_failure | 1/1 | 0/1 |
| unschedulable | 1/1 | 0/1 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 3/5 | 4/5 |
| hard | 0/1 | 0/1 |
| medium | 5/6 | 3/6 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_cm_key_first` | missing_config | Missing key RATE_LIMIT in the ConfigMap referenced by the pod spec. | ✅ | no_action | no_action | ✅ |
| `ho_cpu_limit_rollout` | cpu_throttling | False‑positive anomaly detection due to unusually low resource utilization (memory and CPU) compared to the tr… | ❌ | rollback_deployment | no_action | ❌ |
| `ho_crash_flag_rollout` | app_crash | The pod spec passes the flag `--worker-threads` to the router binary, which does not support it, causing the b… | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `ho_dns_proxy_first` | dependency_connectivity | DNS resolution failure for the service `ledger-db-proxy` (service missing, mis‑named, or in a different namesp… | ✅ | no_action | no_action | ✅ |
| `ho_env_dburl_rollout` | bad_config_value | Malformed DATABASE_URL environment variable (missing colon after protocol). | ✅ | rollback_deployment | no_action | ❌ |
| `ho_healthy_mailer` | healthy | Anomaly detector misinterpreted low resource utilization as an issue; the pod is healthy and functioning corre… | ✅ | no_action | no_action | ✅ |
| `ho_img_quay_rollout` | image_pull | DNS resolution failure on the node to quay.io, likely due to a misconfigured or unhealthy CoreDNS/CoreDNS serv… | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `ho_mem_request_rollout` | unschedulable | The pod’s memory request exceeds the allocatable memory on all available nodes. | ✅ | rollback_deployment | no_action | ❌ |
| `ho_oom_render_first` | oom_killed | The container’s memory usage exceeded the pod’s memory limit, causing the kubelet to OOM kill the process. | ✅ | no_action | no_action | ✅ |
| `ho_pod_kill_two` | pod_disruption | A transient or early‑stage memory leak in the nginx container (or a misconfigured resource limit) is causing a… | ❌ | no_action | no_action | ✅ |
| `ho_readiness_http_rollout` | probe_failure | Readiness probe path is incorrect – nginx serves no /ready resource, so the probe always fails. | ✅ | rollback_deployment | no_action | ❌ |
| `ho_secret_volume_rollout` | missing_config | Missing or mis‑named secret "auth-tls" referenced as a volume. | ✅ | rollback_deployment | no_action | ❌ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 625s and retried 1 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
