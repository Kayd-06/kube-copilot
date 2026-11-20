# LLM eval after the fixes (v2 + held-out)

This compares the PR #31 baseline (`../README.md`, fixtures `cases/`) with the
prompts and context from PR #32 (fixtures `cases_v2/` and `heldout/`). All
runs used Groq's free tier, one run per model, with the same temperature,
labels and scoring code. Every Diagnose answer below was also checked by hand
against the injected fault, using the same rubric as v1.

| Run | Report | Hand checks |
|---|---|---|
| v1, original 38 | `../openai_gpt-oss-*.json` | `ml/data/llm_eval/hand_checks.json` |
| v2, original 38 | `openai_gpt-oss-*.json` (here) | `ml/data/llm_eval/hand_checks_v2.json` |
| Old code, held-out 12 | `../heldout_before/` | `ml/data/llm_eval/hand_checks_heldout_before.json` |
| New code, held-out 12 | `../heldout/` | `ml/data/llm_eval/hand_checks_heldout.json` |

The tables come from `ml/llm_eval/compare.py` (`make eval-compare-all`).

## Which number to trust

The v2 prompts (evidence-weighting rules for Diagnose, rollout-diff rules for
the proposer) were **written after reading the v1 failures on the original 38
cases**, so the v2 scores on those 38 are optimistic. The **held-out set is
the fair number.** Its 12 incidents were written and labelled from the
injected fault before capture, and captured after the prompts were frozen.
Its "before" is the real old code (PR #31 merge, `cc30707`) run on the same
incidents (see `../heldout_before/README.md`).

With 12 cases, the 95% Wilson interval for 12/12 is about 76–100%, for 11/12
about 65–99%, and for 7/12 about 32–81%. On 38 cases the intervals are about
±10–15 points. These are directional numbers from one run each.

## Held-out set (12 incidents): old code vs new code

| | 120b before | 120b after | 20b before | 20b after |
|---|---|---|---|---|
| Root cause, hand-checked | 10/12 | **12/12** | 10/12 | **12/12** |
| Root cause, regex matcher | 9/12 | 10/12 | 8/12 | 9/12 |
| Remediation acceptable | 7/12 | **11/12** | 7/12 | **11/12** |
| Rollout cases where rollback is right | 2/7 | 6/7 | 2/7 | 6/7 |
| Model-free baseline: roll back whenever a previous revision exists | 12/12 | 12/12 | 12/12 | 12/12 |
| Pod kill (Diagnose) | 0/1 | 1/1 | 0/1 | 1/1 |
| CPU throttling (Diagnose) | 0/1 | 1/1 | 0/1 | 1/1 |
| Healthy pod (Diagnose) | 1/1 | 1/1 | 1/1 | 1/1 |
| JSON/schema valid | 12/12 | 12/12 | 12/12 | 12/12 |
| kubectl commands naming a resource not in the context | 0 | 0 | 0 | 0 |
| Proposals blocked by policy / aimed at the wrong object | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Request errors | 0 | 0 | 0 | 0 |
| Diagnose latency p50 / p95 | 1.79 / 2.53 s | 1.72 / 2.01 s | 1.14 / 1.96 s | 1.14 / 1.85 s |

- **Diagnose:** both models with the old code missed the same two incidents.
  `ho_cpu_limit_rollout` was called an idle upstream or an anomaly false
  positive, because the old context has no limits or usage. `ho_pod_kill_two`
  was blamed on a memory trend instead of the pod replacement. The new code
  gets both right.
- **Remediation:** with the old code, both models refused 4 rollbacks whose
  change was not in the image or command (`ho_env_dburl_rollout`,
  `ho_mem_request_rollout`, `ho_readiness_http_rollout`,
  `ho_secret_volume_rollout`). In each case they said the revisions were
  identical. This is the same blind spot as in v1, now confirmed on unseen
  incidents. The new code rolls back all four.
- **Remaining miss (both models, new code):** `ho_cpu_limit_rollout`. The
  agent sees the diff (`limits.cpu: "250m" -> "15m"`) but answers `no_action`,
  because the pod is Running and Ready with no restarts. Diagnose names CPU
  throttling correctly, so only the proposer misses it.
- **The held-out remediation number does not beat a trivial rule.** Every
  held-out incident with a previous revision should be rolled back, so "roll
  back whenever a previous revision exists" scores 12/12 there. The held-out
  set has no trap cases where rolling back would be wrong. Those are only in
  the original 38: there the rule scores 33/38, and v2 scores 37/38 and 38/38.
  A future held-out batch needs rollout traps to test this fairly.
- gpt-oss-20b's proposer needed the `tool_choice=auto` retry on 2 of 12
  calls (`ho_cm_key_first`, `ho_dns_proxy_first`), and 120b needed none.

## Original 38 cases: v1 vs v2 (prompts tuned on these)

| | 120b v1 | 120b v2 | 20b v1 | 20b v2 |
|---|---|---|---|---|
| Root cause, hand-checked | 30/38 | **36/38** | 31/38 | **37/38** |
| Root cause, regex matcher | 27/38 | 35/38 | 27/38 | 33/38 |
| Remediation acceptable | 30/38 | **37/38** | 29/38 | **38/38** |
| Rollout cases where rollback is right | 4/11 | **11/11** | 4/11 | **11/11** |
| Previous revision exists, rollback would not help (no rollback proposed) | 4/4 | 4/4 | 4/4 | 4/4 |
| Model-free baseline: roll back whenever a previous revision exists | 33/38 | 33/38 | 33/38 | 33/38 |
| Pod kills (Diagnose) | 0/3 | 3/3 | 0/3 | 3/3 |
| Healthy pods (Diagnose) | 2/4 | 3/4 | 2/4 | 4/4 |
| CPU throttling (Diagnose) | 0/1 | 1/1 | 0/1 | 1/1 |
| JSON/schema valid | 97.4% | 100% | 97.4% | 100% |
| Diagnoses with a kubectl resource not in the context | 0% | 0% | 0% | 5.3% (2/38) |
| Request errors | 1 | 0 | 2 | 0 |
| Diagnose latency p50 / p95 | 1.77 / 3.42 s | 1.66 / 2.57 s | 1.32 / 2.26 s | 1.09 / 1.61 s |
| Prompt + completion tokens, both tasks | 144K + 33K | 164K + 33K | 143K + 40K | 164K + 44K |

### The 7 previously refused rollbacks

In v1 both models declined these 7 rollouts, because the agent only saw image
and command, and the change was elsewhere in the pod template:
`config_port_rollout`, `liveness_path_rollout`, `missing_cm_envfrom_rollout`,
`missing_secret_key_rollout`, `oom_limit_rollout`,
`pending_nodeselector_rollout` and `readiness_exec_rollout`.

**In v2, all 7 are correct for both models (7/7 and 7/7).** The 4 cases where a
previous revision exists but rolling back would not help (`cm_deleted_after_rollout`,
`cm_edited_after_rollout`, `pod_kill_after_rollout`, `healthy_after_rollout`)
still get no rollback (4/4), so the diff rules did not make the agent roll
back blindly. On the held-out set, the same fix took rollout cases from 2/7 to
6/7 for both models.

### gpt-oss-20b's healthy-pod changes: not a regression

By regex, 20b went from right to wrong on `healthy_web` and
`healthy_job_completed`. By hand, both answers are correct: "False positive
from the anomaly detection model" with a summary saying the pod is healthy,
and "None – the pod finished as intended" with a summary saying the Job
succeeded with exit 0. The matcher has no pattern for these phrasings. 20b's
healthy pods are 4/4 by hand in v2 (2/4 in v1). The regex column above is
lower for 20b than for 120b for this reason.

### Remaining failures (original 38, by hand)

- `stale_dependency` (both models): blames the readiness probe definition
  (`/tmp/ready` never created) instead of the upstream that was down at
  startup. 120b was wrong on this in v1 too. **For 20b this is a real
  regression**, because v1 named the unreachable upstream. 120b's proposer
  also returns no action here, which is its one remediation miss.
- `healthy_noisy_logs` (120b): still treats routine retry warnings as an
  upstream outage. 20b now gets it right.
- 20b's Diagnose referenced two names not in the context in kubectl commands:
  `debug-pod` (a scratch pod it suggests creating) and the name of the
  already-deleted previous pod in `pod_kill_single`. Neither command would
  touch a wrong object, but the hallucination check counts them, and v1 had
  none.
- 20b's proposer needed the `tool_choice=auto` retry on 5 of 38 calls. All
  of them still produced an acceptable answer.

## What changed between v1 and v2 inputs

- **Prompts:** the Diagnose system prompt has evidence-weighting rules
  (lifecycle signals are factual, the anomaly score is weak and needs
  corroboration, CPU throttling, routine warnings). The proposer prompt has
  rollout-diff rules.
- **Context:** structured lifecycle section, container requests and limits,
  Prometheus usage and throttling, compacted logs, and a full pod-template diff
  for the proposer. Prompts are about 13% larger (164K vs 144K prompt tokens
  for the same 76 calls).
- **Fixtures:** 16 cases re-captured (same scenarios and labels), 22 unchanged.
  See `ml/data/llm_eval/README.md`.
- **Scoring:** unchanged (same regexes and remediation rule). The regex
  matcher still misses some correct phrasings and has never credited a wrong
  answer, which is why the hand-checked rows are the headline.

## Caveats

- The held-out set has no rollback-trap cases (see above), so its
  remediation score says the fix finds rollbacks. It does not show that the
  agent avoids wrong ones.
- The prompts were tuned on the original 38, so treat the held-out numbers as
  the honest estimate and the 38-case numbers as an upper bound.
- One run per model, temperature as configured, on a single-node kind
  cluster. The image-pull cases fail on registry DNS because the kind node has
  no registry egress. Naming that as the reason is counted as correct.
- Hand checks are one person's judgment using the written rubric. The notes
  in the JSON files explain every non-obvious verdict.
- The v2 runs on the 38 cases hit Groq's daily token quota and were
  continued with `--resume --wait-daily`, so they span about 19 hours. Pacing and quota waits are
  excluded from latency.
