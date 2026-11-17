# Held-out set, old code ("before")

These are the PR #31 code and prompts (merge commit `cc30707`) run on the same 12
held-out incidents as `../heldout/`, so the held-out set has a real before/after.

How they were produced:

- The held-out fixtures (`ml/data/llm_eval/heldout/`, schema 2) were reduced
  to schema 1 by dropping the fields the old code never had (`usage`,
  container `resources`, `template_diff`, `template_refs`), and retrieval was
  re-run against the v1 runbooks (no `cpu-throttling.md`).
- `python -m ml.llm_eval.run --fixtures <reduced> --out-dir <here>` ran from a
  checkout of `cc30707`, on Groq's free tier, with the same models and
  temperature as the "after" runs.
- Hand checks are in `ml/data/llm_eval/hand_checks_heldout_before.json`.

