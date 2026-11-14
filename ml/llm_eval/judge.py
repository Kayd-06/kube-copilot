"""Optional LLM judge for root-cause correctness, with a fixed rubric.

The deterministic regex matcher is the primary metric. The judge is a second
opinion: it sees the injected fault (ground truth) and the diagnosis, and
answers correct/incorrect. Agreement with the hand-checked verdicts in
ml/data/llm_eval/hand_checks.json is reported for both the matcher and the judge.

    PYTHONPATH=.:backend python -m ml.llm_eval.judge --results ml/reports/llm_eval/openai_gpt-oss-120b.json \
        --judge-model openai/gpt-oss-20b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ml.llm_eval.run import Pacer
from ml.llm_eval.taxonomy import CATEGORIES

HAND_CHECKS = Path(__file__).resolve().parents[1] / "data" / "llm_eval" / "hand_checks.json"

RUBRIC = """You grade a Kubernetes incident diagnosis against the known injected fault.

Answer "correct" only if the diagnosis's likely_root_cause identifies the same
underlying cause as the ground truth (same failure mechanism, e.g. "memory
limit exceeded" for an OOM). It does not need the exact resource names or the
fix. Answer "incorrect" if it names a different cause, only restates a symptom
without the cause (e.g. "the pod is crashlooping" when the cause is a bad env
value), hedges between several unrelated causes, or says the pod is healthy
when it is not (or broken when it is healthy).

Reply with one JSON object: {"verdict": "correct" | "incorrect", "reason": "<one sentence>"}"""


def judge_prompt(label: dict, diagnosis: dict) -> str:
    return json.dumps(
        {
            "ground_truth": {
                "category": label["category"],
                "category_definition": CATEGORIES[label["category"]],
                "injected_fault": label["fault"],
            },
            "diagnosis": {
                "likely_root_cause": diagnosis.get("likely_root_cause"),
                "summary": diagnosis.get("summary"),
            },
        },
        indent=2,
    )


def agreement(a: dict[str, bool], b: dict[str, bool]) -> dict:
    common = sorted(set(a) & set(b))
    agree = [c for c in common if a[c] == b[c]]
    return {"cases": len(common), "agree": len(agree), "rate": round(100 * len(agree) / len(common), 1) if common else None,
            "disagree": [c for c in common if a[c] != b[c]]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--judge-model", default="openai/gpt-oss-20b")
    parser.add_argument("--hand-checks", type=Path, default=HAND_CHECKS)
    parser.add_argument("--tpm", type=int, default=int(os.environ.get("EVAL_TPM", "7000")))
    parser.add_argument("--skip-judge", action="store_true", help="Only compare the regex matcher with the hand checks")
    args = parser.parse_args(argv)

    report = json.loads(args.results.read_text())
    recs = [r for r in report["records"] if r["task"] == "diagnose" and r["variant"] == "full" and "error" not in r]
    matcher = {r["case"]: bool(r["category"]["correct"]) for r in recs}
    hand_all = json.loads(args.hand_checks.read_text()) if args.hand_checks.exists() else {}
    hand = {c: v["correct"] for c, v in hand_all.get(report["model"], {}).items()}

    judge: dict[str, bool] = {}
    verdicts = {}
    if not args.skip_judge:
        from openai import OpenAI

        key = os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY")
        if not key:
            print("LLM_API_KEY / GROQ_API_KEY is not set", file=sys.stderr)
            return 2
        client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1"), api_key=key,
                        timeout=60.0, max_retries=0)
        pacer = Pacer(args.tpm)
        for r in recs:
            label = report["labels"][r["case"]]

            def call(label=label, r=r):
                return client.chat.completions.create(
                    model=args.judge_model, temperature=0.0, response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": RUBRIC},
                              {"role": "user", "content": judge_prompt(label, r["diagnosis"])}],
                )

            resp, _ = pacer.call(call, 1500)
            usage = getattr(resp, "usage", None)
            pacer.after(getattr(usage, "total_tokens", 0) or 1500)
            try:
                out = json.loads(resp.choices[0].message.content or "{}")
                judge[r["case"]] = out.get("verdict") == "correct"
                verdicts[r["case"]] = out
            except json.JSONDecodeError:
                verdicts[r["case"]] = {"verdict": "unparseable"}
            print(r["case"], verdicts[r["case"]].get("verdict"), flush=True)

    result = {
        "results": str(args.results),
        "model": report["model"],
        "judge_model": None if args.skip_judge else args.judge_model,
        "hand_checked_cases": len(hand),
        "matcher_accuracy": round(100 * sum(matcher.values()) / len(matcher), 1) if matcher else None,
        "hand_accuracy": round(100 * sum(hand.values()) / len(hand), 1) if hand else None,
        "judge_accuracy": round(100 * sum(judge.values()) / len(judge), 1) if judge else None,
        "matcher_vs_hand": agreement(matcher, hand),
        "judge_vs_hand": agreement(judge, hand) if judge else None,
        "judge_vs_matcher": agreement(judge, matcher) if judge else None,
        "judge_verdicts": verdicts,
    }
    out = args.results.with_name(args.results.stem + ".judge.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "judge_verdicts"}, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
