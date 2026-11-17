# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-17T20:12:31+00:00 → 2026-11-17T20:28:24+00:00 (UTC) on 12 frozen incidents from `ml/data/llm_eval/cases/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 9/12 = 75.0% (95% CI (46.8, 91.1)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.79s / 2.53s |
| Mean confidence when right / wrong | 0.911 / 0.783 |
| Remediation (first proposed action) correct | 7/12 = 58.3% (95% CI (32.0, 80.7)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/2 |
| Propose latency p50 / p95 | 0.85s / 1.69s |
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
| diagnose/full | 12 | 0 | 75.0% | 1.79s | 2.53s | 30995 | 7514 | 0.0092 |
| propose/full | 12 | 0 | 58.3% | 0.85s | 1.69s | 21330 | 2995 | 0.005 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| app_crash | 1/1 | 1/1 |
| bad_config_value | 1/1 | 0/1 |
| cpu_throttling | 0/1 | 0/1 |
| dependency_connectivity | 1/1 | 1/1 |
| healthy | 1/1 | 1/1 |
| image_pull | 1/1 | 1/1 |
| missing_config | 1/2 | 1/2 |
| oom_killed | 1/1 | 1/1 |
| pod_disruption | 0/1 | 1/1 |
| probe_failure | 1/1 | 0/1 |
| unschedulable | 1/1 | 0/1 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| easy | 4/5 | 4/5 |
| hard | 0/1 | 0/1 |
| medium | 5/6 | 3/6 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_cm_key_first` | missing_config | The referenced ConfigMap exists but does not contain the required key RATE_LIMIT, causing the pod spec validat… | ❌ | no_action | no_action | ✅ |
| `ho_cpu_limit_rollout` | cpu_throttling | Upstream input source (e.g., message queue or data feed) is unavailable or mis‑configured, causing the encoder… | ❌ | rollback_deployment | no_action | ❌ |
| `ho_crash_flag_rollout` | app_crash | The pod spec passes an unsupported flag (--worker-threads) to the router binary, causing it to exit immediatel… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_dns_proxy_first` | dependency_connectivity | The pod cannot resolve the DNS name 'ledger-db-proxy' (service or endpoint missing or mis‑configured), so the … | ✅ | no_action | no_action | ✅ |
| `ho_env_dburl_rollout` | bad_config_value | Malformed DATABASE_URL environment variable (incorrect scheme syntax) causing the application to exit immediat… | ✅ | rollback_deployment | no_action | ❌ |
| `ho_healthy_mailer` | healthy | A false‑positive anomaly detection – the pod’s resource usage is far below its limits, which the model interpr… | ✅ | no_action | no_action | ✅ |
| `ho_img_quay_rollout` | image_pull | DNS resolution failure on the node (or cluster DNS) when trying to reach quay.io, preventing the image from be… | ✅ | rollback_deployment | rollback_deployment | ✅ |
| `ho_mem_request_rollout` | unschedulable | The pod’s memory request (or limit) exceeds the allocatable memory on the only available node, and the node is… | ✅ | rollback_deployment | no_action | ❌ |
| `ho_oom_render_first` | oom_killed | The container runs out of its allocated memory during the rendering task, triggering an OOM kill (exit code 13… | ✅ | no_action | no_action | ✅ |
| `ho_pod_kill_two` | pod_disruption | A memory consumption trend (possible leak or unbounded cache) in the nginx container that is rising quickly, w… | ❌ | no_action | no_action | ✅ |
| `ho_readiness_http_rollout` | probe_failure | Misconfigured readiness probe – the probe path (/ready) does not exist in the nginx container image. | ✅ | rollback_deployment | no_action | ❌ |
| `ho_secret_volume_rollout` | missing_config | Missing secret "auth-tls" referenced by the pod’s volume definition. | ✅ | rollback_deployment | no_action | ❌ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 591s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
