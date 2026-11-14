# LLM eval after the fixes (v2 + held-out)

This compares the PR #31 baseline (`../README.md`, fixtures `cases/`) with the
fixed prompts and context from PR #32 (fixtures `cases_v2/` and `heldout/`).
Runs were on Groq's free tier on 2026-11-14 (UTC). Labels were not changed.

> **Status: partial.** The v2 re-runs on the original 38 cases ran out of Groq's
> daily token quota (200K tokens/day/model, refilling at about 10K/hour)
> mid-run. When this was written, **gpt-oss-20b had finished all 38 diagnoses
> and 6 of 38 proposals, and gpt-oss-120b had finished 21 of 38 diagnoses and no
> proposals.** The processes were left running with `--wait-daily` and write
> `openai_gpt-oss-*.json/.md` here when they finish or give up. Finish them
> with `make eval EVAL_ARGS="--resume --wait-daily 8"` (and
> `LLM_MODEL=openai/gpt-oss-20b`). The v2 numbers below come from the per-call
> lines the runner printed (`partial_logs/`). They are **regex-matcher
> verdicts only and not hand-checked yet**, because the diagnosis texts are only
> written when a run ends.

## Held-out set (12 new incidents, complete, hand-checked)

These incidents were written and labelled from the injected fault and
committed before capture, and captured after the prompt changes were frozen,
so this is the least biased number in the PR. Only `gpt-oss-120b` was run on
it (quota). There is **no "before" number on the held-out set**: the old-code
baseline (recipe in `ml/data/llm_eval/README.md`) was prepared but
not run for lack of quota.

| `openai/gpt-oss-120b`, held-out | Result |
|---|---|
| Root cause, hand-checked | **12/12** |
| Root cause, regex matcher | 10/12 (the two misses are phrasing: "does not contain the key", "Mis-configured DATABASE_URL") |
| Remediation acceptable | **11/12** |
| Rollout cases where rollback is right | 6/7 |
| JSON/schema valid | 12/12 |
| kubectl commands naming a resource not in the context | 0 |
| Proposals blocked by policy / aimed at the wrong object | 0 / 0 |
| Request errors | 0 |
| Diagnose p50 / p95 | 1.72 s / 2.01 s |
| Tokens (prompt + completion), both tasks | 52,253 + 10,566 |

With 12 cases, the 95% Wilson interval for 12/12 is about 76–100%, and for
11/12 it is about 65–99%.

The one remediation miss is `ho_cpu_limit_rollout`. The agent saw the diff
(`limits.cpu: "250m" -> "15m"`) but answered `no_action`, because the pod is
Running and Ready with no restarts. Diagnose itself named CPU throttling
correctly.

## Original 38 cases: v1 to v2 (partial, regex only)

Regex verdicts on the cases each v2 run finished, against the v1 run on
the **same cases**. In v1 the regex matcher never credited a wrong answer,
but it missed 3–4 right answers per model on phrasing. So a v2 "wrong" can be a
right answer the matcher missed, and the hand check is still pending.

| | v1 regex | v1 hand-checked | v2 regex |
|---|---|---|---|
| gpt-oss-20b, Diagnose, all 38 | 27/38 | 31/38 | **33/38** |
| gpt-oss-120b, Diagnose, first 21 (alphabetical) | 15/21 | 17/21 | **19/21** |
| gpt-oss-20b, remediation, first 6 | 5/6 | – | 6/6 |
| gpt-oss-120b, remediation | – | – | not run yet (quota) |

Per failure mode (regex, v1 to v2):

| Failure mode from PR #31 | gpt-oss-20b | gpt-oss-120b |
|---|---|---|
| Pod kills (Diagnose) | 0/3 to **3/3** | pending |
| CPU throttling (Diagnose) | 0/1 to **1/1** | 0/1 to **1/1** |
| Healthy pods (Diagnose) | v1 regex 1/4 (hand 2/4) to v2 regex 2/4 | v1 regex 2/4 (hand 2/4) to v2 regex **3/4** (`healthy_after_rollout` fixed, `healthy_noisy_logs` still wrong) |
| Prompt over 8K TPM (413 on `liveness_path_rollout`) | error to answered, regex-correct | error to answered, regex-correct |
| Rollout cases (remediation) | `config_port_rollout`: `no_action` to `rollback_deployment` (1 of 7 v1 misses re-run so far) | pending |

20b Diagnose regressions by regex that still need a hand check:
`healthy_web` and `healthy_job_completed` (v1 hand: right), plus
`stale_dependency`, `config_port_rollout` and `crash_traceback_first` (v1 hand:
right, regex: wrong in both runs). 120b's `healthy_noisy_logs` (routine retry
warnings) is still wrong.

## What changed between v1 and v2 inputs

- **Prompts:** the Diagnose system prompt now has evidence-weighting rules
  (lifecycle signals are factual, the anomaly score is weak and needs
  corroboration, CPU throttling, routine warnings). The proposer prompt has
  rollout-diff rules. These were **written after reading the v1 failures on
  these 38 cases**, so v2 scores on the original set are optimistic. The
  held-out set exists for that reason.
- **Context:** structured lifecycle section, container requests/limits,
  Prometheus usage/throttling, compacted logs, and a full pod-template diff for
  the proposer.
- **Fixtures:** 16 cases re-captured (same scenarios and labels), 22 unchanged.
  See `ml/data/llm_eval/README.md`.
- **Scoring:** unchanged (same regexes and remediation rule).

## Not run

- The old-code baseline on the held-out set, and gpt-oss-20b on the held-out set.
- The rest of the v2 runs listed above, and the v2 hand checks
  (`ml/data/llm_eval/hand_checks_v2.json` does not exist yet).
