# LLM eval results

The 38 frozen incidents in [`ml/data/llm_eval`](../../data/llm_eval/README.md)
were replayed through the API's own `LLMExplainer` (Diagnose) and
`ActionProposer` (safe actions) on Groq on 2026-11-13, between 18:01 and 18:39
UTC, using the production prompts and temperatures. Each model got one call per
case and task. Per-call records are in `<model>.json`, readable reports in
`<model>.md`, and the judge results in `<model>.judge.json`.

## Results

| | `openai/gpt-oss-120b` (default) | `openai/gpt-oss-20b` |
|---|---|---|
| Root cause, hand-checked | **30/38 (78.9%)** | **31/38 (81.6%)** |
| Root cause, regex matcher (deterministic) | 27/38 (71.1%, 95% CI 55–83) | 27/38 (71.1%, 95% CI 55–83) |
| Root cause, LLM judge (`qwen/qwen3.8-27b`) | 26/38 (68.4%) | 29/38 (76.3%) |
| Remediation: first proposed action acceptable | **30/38 (78.9%, 95% CI 64–89)** | **29/38 (76.3%, 95% CI 61–87)** |
| JSON/schema valid | 37/38 (97.4%; the miss is a request error) | 37/38 (97.4%; same) |
| kubectl commands naming a resource not in the context | 0 of 310 refs checked | 0 of 299 refs checked |
| Proposals blocked by the policy engine / aimed at the wrong object | 0 / 0 (4 proposals) | 0 / 0 (4 proposals) |
| Request errors | 1 (HTTP 413, prompt over the 8K TPM limit) | 2 (the same 413, and one 400 "tool choice required") |
| Diagnose latency p50 / p95 | 1.77 s / 3.42 s | 1.32 s / 2.26 s |
| Propose latency p50 / p95 | 0.96 s / 2.36 s | 0.85 s / 1.49 s |
| Tokens (prompt + completion), both tasks | 144,478 + 33,287 | 142,538 + 40,133 |
| Cost at Groq list price | $0.042 | $0.023 |

Errors count as wrong in every accuracy above. The 95% intervals (Wilson) are
wide: with 38 cases, the two models are statistically tied.

**Agreement with the hand check** (37 diagnoses per model, leaving out the
request errors):

- **Regex matcher:** 34/37 (91.9%) for 120b and 33/37 (89.2%) for 20b. All of
  its disagreements are false negatives on phrasings it doesn't cover, such as
  "does not recognize", "does not contain the key", "KeyError" and "not an
  actual pod failure". It never credited a wrong answer.
- **LLM judge:** 33/37 (89.2%) for 120b and 35/37 (94.6%) for 20b. It is
  stricter than the hand check on the image-pull cases. There the evidence the
  cluster produced is a registry DNS failure rather than "tag not found",
  because the box's kind node has no registry egress. It is also stricter on
  vague dependency answers.

Re-scoring note: after the first run, the matcher was changed to normalize
Unicode hyphens. The models write `non‑numeric` with U+2011, which the regex
missed. This flipped one verdict, `config_port_rollout` for 120b, from wrong to
right. No other matcher change was made after seeing outputs. `make eval
EVAL_ARGS=--rescore` re-applies the matchers without API calls.

## Model-free baselines on the same cases

| Baseline | Accuracy |
|---|---|
| Category from a pod-reason lookup table | 21/38 (55.3%) |
| Remediation: always `no_action` | 26/38 (68.4%) |
| Remediation: roll back if a previous revision exists, else `no_action` | 33/38 (86.8%) |
| RAG: an expected runbook in the top 5 (deterministic retriever, 29 cases with a matching runbook) | 25/29 (86.2%), hit@1 69.0%, MRR 0.738 |

The rollback heuristic beats both models on remediation. The main reason is a
bug the eval found, described below.

## Failure modes

1. **The proposer can't see what changed between revisions.** Both models were
   wrong on 7 of the 11 "rollout" cases, and all 7 came from the same cause.
   `template_summary` in `backend/app/actions/cluster.py` only passes image,
   command and args to the LLM, so a rollout that changed env, `envFrom`,
   resources, probes or `nodeSelector` looks identical to the previous
   revision. The model says exactly that ("current and previous revisions are
   identical") and declines to roll back. Image and command rollouts were 4/4.
   The fix is to include env/envFrom, resources, probes, volumes,
   nodeSelector/affinity and init containers in the summary, then re-capture
   and re-run.
2. **Pod kills are ignored in Diagnose: 0/3 for both models.** The lifecycle
   signals (`Killing`, `pod_replaced`) are in the prompt, but the models either
   call the replacement pod healthy or follow the metric anomaly. The proposer
   still picks the right `no_action` for all three.
3. **False-positive anomalies mislead Diagnose.** The synthetic-trained Isolation
   Forest flags several healthy pods. Each model got 2 of the 4 healthy cases
   wrong. After a routine rollout, both blamed a "rising memory footprint". A
   worker that logs routine retry warnings was diagnosed as an upstream outage
   by both.
4. **CPU throttling: 0/1 for both models.** Pod limits are not in the
   Diagnose context, and no runbook covers throttling.
5. **Prompt size is unbounded.** A pod restarted by a failing liveness probe
   produced about 19.7K characters of nginx logs. The 9.6K-token request was
   rejected (413) by the Groq free tier's 8K TPM limit for both models, so
   Diagnose would fail the same way for a user on that tier.
6. **Confidence is not informative.** Mean self-reported confidence was 0.90
   when the answer was right and 0.83 (120b) or 0.81 (20b) when it was wrong.
7. **Retrieval misses.** The four misses are a DNS case (crashloop runbooks
   rank above `dns-issues.md`), two probe cases where `crashloopbackoff.md`
   outranks `probe-failures.md`, and the stuck-dependency case. Cases with no
   matching runbook (CPU throttling, port mismatch, pod kills, healthy) are
   left out of the hit rate.

## Not run (quota)

The without-RAG and without-lifecycle-signals ablations (`make
eval-ablations`) are implemented but were **not run**. Each full run used
about 180K of the free tier's 200K tokens per day per model. Run them once the
quota allows, or with a Developer-tier key.
