# LLM eval: `openai/gpt-oss-120b`

Run 2026-11-19T12:19:55+00:00 → 2026-11-19T12:23:05+00:00 (UTC) on 5 frozen incidents from `/workspace/kube-copilot/ml/data/llm_eval/traps_before/`. Status: **complete**.

## Headline (full context)

| Metric | Value |
|---|---|
| Root-cause category accuracy | 4/5 = 80.0% (95% CI (37.6, 96.4)) |
| JSON/schema valid | 100.0% |
| Hallucinated resource in a kubectl command | 0.0% of diagnoses |
| Diagnose latency p50 / p95 | 1.66s / 1.82s |
| Mean confidence when right / wrong | 0.93 / 0.9 |
| Remediation (first proposed action) correct | 5/5 = 100.0% (95% CI (56.6, 100.0)) |
| Proposals targeting the wrong object | 0 |
| Proposals the policy engine would block | 0/0 |
| Propose latency p50 / p95 | 0.82s / 1.07s |
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
| diagnose/full | 5 | 0 | 80.0% | 1.66s | 1.82s | 12420 | 2850 | 0.0036 |
| propose/full | 5 | 0 | 100.0% | 0.82s | 1.07s | 9058 | 1270 | 0.0021 |

## By category (full context)

| Category | Diagnose | Propose |
|---|---|---|
| bad_config_value | 1/1 | 1/1 |
| dependency_connectivity | 1/1 | 1/1 |
| healthy | 0/1 | 1/1 |
| missing_config | 1/1 | 1/1 |
| unschedulable | 1/1 | 1/1 |

## By difficulty (full context)

| Difficulty | Diagnose | Propose |
|---|---|---|
| hard | 2/3 | 3/3 |
| medium | 2/2 | 2/2 |

## Per case (full context)

| Case | Label | Diagnose root cause (truncated) | Cat ok | Expected action | Proposed | Act ok |
|---|---|---|---|---|---|---|
| `ho_trap_configmap_reload` | bad_config_value | Invalid configuration value (max_discount_pct set to a non‑integer) in engine.properties causing the container… | ✅ | no_action | no_action | ✅ |
| `ho_trap_dependency_down` | dependency_connectivity | The container cannot reach the dependent ledger service (mis‑configured URL, missing Service/Endpoints, or the… | ✅ | no_action | no_action | ✅ |
| `ho_trap_healthy_retry_logs` | healthy | Intermittent failures of the external partner‑api service that feed‑sync contacts, causing repeated timeout an… | ❌ | no_action | no_action | ✅ |
| `ho_trap_node_cordoned` | unschedulable | The only node (n/a) is cordoned or otherwise marked unschedulable (e.g., NotReady, tainted without tolerations… | ✅ | no_action | no_action | ✅ |
| `ho_trap_secret_deleted` | missing_config | Missing secret "smtp-credentials" referenced by the pod spec. | ✅ | no_action | no_action | ✅ |

## How this is scored

- **Category**: the hand-assigned category's regex (ml/llm_eval/taxonomy.py) must match `likely_root_cause`.
- **Remediation**: the first proposed action (or `no_action` when none) must be in the case's acceptable set.
- **Schema**: the raw reply must be one JSON object with the five keys and right types.
- **Hallucination**: namespaces/resource names in `kubectl_commands` that do not appear in the prompt. Placeholders like `<svc>` are counted separately, not as hallucinations.
- **Latency** is the API round trip of the successful attempt (pacing/429 waits excluded). Pacing waited 177s and retried 0 rate-limited calls.
- **Cost** uses Groq list prices (console.groq.com/docs/models, checked 2026-11-13).
