# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-14T08:36:53+00:00 → 2026-11-14T08:46:17+00:00 (UTC) on 12 frozen incidents from `ml/data/llm_eval/heldout/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 10/12 = 83.3% (95% CI (55.2, 95.3)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.72s / 2.01s |
| Mean confidence when right / wrong | 0.91 / 0.95 |
| Remediation (first proposed action) correct | 11/12 = 91.7% (95% CI (64.6, 98.5)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/6 |
| Propose latency p50 / p95 | 0.94s / 1.25s |
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
| diagnose/full | 12 | 0 | 83.3% | 1.72s | 2.01s | 30935 | 7044 | 0.0089 |
| propose/full | 12 | 0 | 91.7% | 0.94s | 1.25s | 21318 | 3522 | 0.0053 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 1/1 | 1/1 |
| bad_config_value | 0/1 | 1/1 |
| cpu_throttling | 1/1 | 0/1 |
| dependency_connectivity | 1/1 | 1/1 |
| healthy | 1/1 | 1/1 |
| image_pull | 1/1 | 1/1 |
| missing_config | 1/2 | 2/2 |
| oom_killed | 1/1 | 1/1 |
| pod_disruption | 1/1 | 1/1 |
| probe_failure | 1/1 | 1/1 |
| unschedulable | 1/1 | 1/1 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 3/5 | 5/5 |
| hard | 1/1 | 0/1 |
| medium | 6/6 | 6/6 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_cm_key_first` | missing_config | The ConfigMap kc-ho-11/gateway-limits does not contain the required key RATE_LIMIT, causing the kubelet to rej… | ❌ | no_action | no_action | ✅ |
| `ho_cpu_limit_rollout` | cpu_throttling | CPU throttling due to an insufficient CPU limit for the encoder container | ✅ | rollback_deployment | no_action | ❌ |
| `ho_crash_flag_rollout` | app_crash | Invalid command‑line flag (--worker-threads) passed to the router binary, causing the container to exit immedi… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_dns_proxy_first` | dependency_connectivity | Missing or unreachable ledger-db-proxy service (DNS resolution or network connectivity failure) causing the co… | ✅ | no_action | no_action | ✅ |
| `ho_env_dburl_rollout` | bad_config_value | Mis‑configured DATABASE_URL environment variable causing the container to exit immediately (CrashLoopBackOff). | ❌ | rollback_deployment | rollback_deployment | ✅ |
| `ho_healthy_mailer` | healthy | False positive anomaly detection; the pod is healthy and operating normally. | ✅ | no_action | no_action | ✅ |
| `ho_img_quay_rollout` | image_pull | Cluster DNS (CoreDNS) is misbehaving or unreachable, causing DNS resolution failure for quay.io and preventing… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_mem_request_rollout` | unschedulable | Pod’s memory request exceeds the allocatable memory on the only available node, leaving no node that can sched… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_oom_render_first` | oom_killed | Memory limit too low – the pdf-render container runs out of memory and is killed by the kubelet (OOMKilled). | ✅ | no_action | no_action | ✅ |
| `ho_pod_kill_two` | pod_disruption | External disruption (e.g., manual delete, node drain, or a controller‑initiated restart) caused the previous m… | ✅ | no_action | no_action | ✅ |
| `ho_readiness_http_rollout` | probe_failure | Readiness probe points to a non‑existent URL (/ready) in the nginx container, causing the probe to fail and th… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_secret_volume_rollout` | missing_config | Missing secret "auth-tls" referenced by the pod’s volume mount, causing a FailedMount event. | ✅ | rollback_deployment | rollback_deployment | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 533s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
