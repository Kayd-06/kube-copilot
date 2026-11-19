# LLM eval: `openai/gpt-oss-20b`

Run 2026-11-19T12:19:55+00:00 → 2026-11-19T12:23:06+00:00 (UTC) on 5 frozen incidents from `/workspace/kube-copilot/ml/data/llm_eval/traps_before/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 4/5 = 80.0% (95% CI (37.6, 96.4)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.04s / 2.3s |
| Mean confidence when right / wrong | 0.85 / 0.9 |
| Remediation (first proposed action) correct | 4/5 = 80.0% (95% CI (37.6, 96.4)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/1 |
| Propose latency p50 / p95 | 0.58s / 1.15s |
| RAG: correct runbook in top-5 | 100.0% of 4 cases (hit@1 75.0%, MRR 0.833) |

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 60.0% |
| Remediation: always `no_action` | 100.0% |
| Remediation: rollback if a previous revision exists, else `no_action` | 0.0% |

## All runs

| Task / variant | Cases | Errors | Accuracy | p50 | p95 | Prompt tok | Completion tok | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| diagnose/full | 5 | 0 | 80.0% | 1.04s | 2.3s | 12420 | 3600 | 0.002 |
| propose/full | 5 | 0 | 80.0% | 0.58s | 1.15s | 9058 | 1970 | 0.0013 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| bad_config_value | 1/1 | 1/1 |
| dependency_connectivity | 0/1 | 0/1 |
| healthy | 1/1 | 1/1 |
| missing_config | 1/1 | 1/1 |
| unschedulable | 1/1 | 1/1 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| hard | 2/3 | 2/3 |
| medium | 2/2 | 2/2 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_trap_configmap_reload` | bad_config_value | Invalid configuration value in /etc/quote/engine.properties (max_discount_pct=thirty). | ✅ | no_action | no_action | ✅ |
| `ho_trap_dependency_down` | dependency_connectivity | The pod spec uses the wrong image/command – it runs a curl image that exits immediately because it cannot reac… | ❌ | no_action | restart_deployment | ❌ |
| `ho_trap_healthy_retry_logs` | healthy | Anomaly detector false positive – the pod is healthy and functioning normally. | ✅ | no_action | no_action | ✅ |
| `ho_trap_node_cordoned` | unschedulable | The pod’s resource requests exceed the allocatable resources on the only available node (or node constraints s… | ✅ | no_action | no_action | ✅ |
| `ho_trap_secret_deleted` | missing_config | The secret "smtp-credentials" referenced by the pod does not exist in the namespace kc-ho-16. | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 181s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
