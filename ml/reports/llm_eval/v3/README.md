# LLM eval v3 (PR #34): dependency check, health summary, resource regressions, rollback traps

"Before" is the PR #33 code (`eval-v2-full` @ `edb78e5`). "After" is this PR.
All runs were on Groq's free tier, one run per model, with the same scoring
code. Every Diagnose answer was hand-checked against the injected fault.

| Run | Report | Hand checks |
|---|---|---|
| PR #33, held-out 12 | `../heldout/` | `hand_checks_heldout.json` |
| PR #33, 5 traps | `../traps_before/` | `hand_checks_traps_before.json` |
| PR #34, held-out 12 + 5 traps | `../heldout_v3/` | `hand_checks_heldout_v3.json` |
| PR #33, original 38 | `../v2/` | `hand_checks_v2.json` |
| PR #34, original 38 (**partial**) | `./` | `hand_checks_v3.json` |

Hand-check files are in `ml/data/llm_eval/`. The trap rubric
(`trap_rubric.md`) was committed before any model saw the traps. Tables come
from `make eval-compare-all`.

## Held-out (12 incidents + 5 rollback traps = 17)

| | 120b before | 120b after | 20b before | 20b after |
|---|---|---|---|---|
| Root cause, hand-checked | 16/17 | 16/17 | 15/17 | **17/17** |
| Root cause, regex | 14/17 | 17/17 | 13/17 | 14/17 |
| Remediation acceptable | 16/17 | 16/17 | 15/17 | **16/17** |
| Rollouts where rollback is right (7) | 6/7 | 6/7 | 6/7 | 6/7 |
| Traps: acceptable action (`no_action`) | 5/5 | 5/5 | 4/5 | 5/5 |
| Traps: rollback avoided | 5/5 | 5/5 | 5/5 | 5/5 |
| Baseline: roll back whenever a previous revision exists | 12/17 | 12/17 | 12/17 | 12/17 |
| Request errors | 0 | 0 | 0 | 0 |

The "before" column joins two runs: the PR #33 held-out run on the original
`heldout/` fixtures and the PR #33 code run on the traps. The "after" run uses
`heldout_v3/`, where `ho_dns_proxy_first` was re-captured and retrieval was
re-run for all 17 fixtures.

**Flips.**

| Model | Task | Case | Change |
|---|---|---|---|
| 120b | Remediation | `ho_cpu_limit_rollout` | wrong → right (rolls back the CPU-limit cut) |
| 120b | Diagnose | `ho_trap_healthy_retry_logs` | wrong → right (borderline, see the hand-check note) |
| 120b | Remediation | `ho_secret_volume_rollout` | right → wrong (regression) |
| 120b | Diagnose | `ho_pod_kill_two` | right → wrong (regression) |
| 20b | Remediation | `ho_cpu_limit_rollout` | wrong → right |
| 20b | Diagnose and remediation | `ho_trap_dependency_down` | wrong → right (`restart_deployment` → `no_action`) |
| 20b | Diagnose | `ho_trap_node_cordoned` | wrong → right |
| 20b | Remediation | `ho_img_quay_rollout` | right → wrong (regression) |

The regressions:

- `ho_secret_volume_rollout` (120b): the rollout added a mount of a Secret
  that doesn't exist. The model now says "create the secret, rollback won't
  help". It looks like the new "a dependency is down, so don't roll back"
  rule spills over to missing references the rollout itself added.
- `ho_pod_kill_two` (120b): blames "a recent rollout or external disruption"
  and leads with the rollout, but there was no rollout.
- `ho_img_quay_rollout` (20b): treats the registry DNS failure as an
  external dependency and answers `no_action` instead of rolling back the
  image change.

**The traps don't separate the PR #33 code from this PR on rollbacks.** Both
code versions avoided rollback on all 5 traps for both models, so the
existing "unrelated change, don't roll back" rule already handled them. What
the traps do: they stop the trivial "roll back whenever a previous revision
exists" rule from looking perfect (12/17 instead of 12/12). They also caught
one wrong action with the PR #33 code: 20b restarted a client whose upstream
was down.

## Original 38: PARTIAL (daily quota)

Both runs hit Groq's 200K tokens/day limit while still in Diagnose:

- **gpt-oss-120b**: 30 of 76 calls, all of them Diagnose. No proposer calls ran.
- **gpt-oss-20b**: 25 of 76 calls. A `--resume --wait-daily` run is still
  going and is **not** included here.

On the cases that were covered, hand-checked root cause, PR #33 → PR #34:

| | Cases covered | PR #33 | PR #34 |
|---|---|---|---|
| gpt-oss-120b | 30 (alphabetical, `cm_deleted_after_rollout` … `pod_kill_after_rollout`) | 29/30 | 30/30 |
| gpt-oss-20b | 25 (`cm_deleted_after_rollout` … `oom_leak_first`) | 25/25 | 25/25 |

- The only flip is `healthy_noisy_logs` on 120b, from wrong to right. It is
  borderline: the summary says the pod is healthy and the retries succeed,
  and the root cause says the timeouts don't stop the work. But the model
  still suggests looking at the upstream. Under a strict reading it is
  unchanged (wrong).
- There were no regressions on the covered diagnoses.
- **`stale_dependency` was not reached by either run**, so fix 1 has **no
  measured result** on the case it targets. The fixture shows the new
  evidence, for example `deps-api ... 1/1 backing pods Ready ... DEPENDENCY
  RECOVERED AFTER THIS POD STARTED`, but no model answer has been scored on it.
- The proposer was not run on the original 38, so rollouts, traps and
  remediation have no v3 numbers there yet.

## Caveats

- These are one run per model. 17 held-out cases give wide intervals (16/17
  is about 73–99% Wilson).
- The prompts were written by the same person who wrote the traps, and the
  traps were written before the prompt changes were run. The fixes target
  failures already seen on `ho_cpu_limit_rollout` and on the 38-case set, so
  those cases are no longer truly held out.
- The v3 re-captures ran on a rebuilt box. CoreDNS was scaled to 1 replica,
  because the host lacks the iptables `statistic` match (see the dataset
  README).
- The two healthy-with-noisy-logs verdicts are borderline, and both are
  counted right. The notes explain why.
