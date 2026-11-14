"""Before/after tables from saved eval reports and hand checks (no API calls).

    PYTHONPATH=.:backend python -m ml.llm_eval.compare \\
        --before ml/reports/llm_eval/openai_gpt-oss-120b.json --before-hand ml/data/llm_eval/hand_checks.json \\
        --after ml/reports/llm_eval/v2/openai_gpt-oss-120b.json --after-hand ml/data/llm_eval/hand_checks_v2.json

Diagnose is scored by the hand check when one exists for the case (otherwise
the regex matcher); request errors count as wrong.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(report_path: Path, hand_path: Path | None) -> dict[str, Any]:
    report = json.loads(report_path.read_text())
    hand = {}
    if hand_path and hand_path.exists():
        hand = json.loads(hand_path.read_text()).get(report["model"], {})
    diag, prop, errors, retried = {}, {}, [], []
    for r in report["records"]:
        if r["variant"] != "full":
            continue
        if "error" in r:
            errors.append(f"{r['task']}/{r['case']}: {r['error'][:60]}")
        if r["task"] == "diagnose":
            regex = bool(r.get("category", {}).get("correct"))
            h = hand.get(r["case"], {}).get("correct") if "error" not in r else False
            diag[r["case"]] = {"regex": regex, "hand": h, "final": regex if h is None else h}
        else:
            prop[r["case"]] = bool(r.get("remediation", {}).get("correct"))
            if r.get("retried_without_required_tool"):
                retried.append(r["case"])
    return {"model": report["model"], "labels": report["labels"], "diag": diag, "prop": prop,
            "errors": errors, "retried": retried, "hand_cases": sum(1 for v in diag.values() if v["hand"] is not None)}


# Cases in cases.py with a healthy previous revision where rolling back would not help.
NO_ROLLBACK_TRAPS = {"cm_deleted_after_rollout", "cm_edited_after_rollout", "pod_kill_after_rollout",
                     "healthy_after_rollout"}
GROUPS = {
    "Rollout cases (rollback expected)": ("prop", lambda lab: "rollback_deployment" in lab["expected_actions"]),
    "Previous revision exists, rollback would not help": (
        "prop", lambda lab: lab.get("_id") in NO_ROLLBACK_TRAPS),
    "Pod kills (Diagnose)": ("diag", lambda lab: lab["category"] == "pod_disruption"),
    "Healthy pods (Diagnose)": ("diag", lambda lab: lab["category"] == "healthy"),
    "CPU throttling (Diagnose)": ("diag", lambda lab: lab["category"] == "cpu_throttling"),
}


def score(run: dict[str, Any], kind: str, cases: list[str]) -> tuple[int, int]:
    if kind == "diag":
        return sum(run["diag"].get(c, {}).get("final", False) for c in cases), len(cases)
    return sum(run["prop"].get(c, False) for c in cases), len(cases)


def table(before: dict[str, Any] | None, after: dict[str, Any]) -> str:
    labels = {c: {**lab, "_id": c} for c, lab in after["labels"].items()}
    cases = sorted(labels)
    cols = ["Before", "After"] if before else ["Result"]
    lines = [f"| | {' | '.join(cols)} |", "|---|" + "---|" * len(cols)]

    def row(name: str, fn) -> None:
        vals = []
        for run in ([before] if before else []) + [after]:
            n, d = fn(run)
            vals.append(f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a")
        lines.append(f"| {name} | {' | '.join(vals)} |")

    row("Root cause (hand-checked where available)", lambda r: score(r, "diag", cases))
    row("Root cause (regex matcher only)", lambda r: (sum(r["diag"].get(c, {}).get("regex", False) for c in cases),
                                                      len(cases)))
    row("Remediation acceptable", lambda r: score(r, "prop", cases))
    for name, (kind, pred) in GROUPS.items():
        group = [c for c in cases if pred(labels[c])]
        if group:
            row(f"{name}", lambda r, g=group, k=kind: score(r, k, g))
    vals = [str(len(r["errors"])) for r in ([before] if before else []) + [after]]
    lines.append(f"| Request errors | {' | '.join(vals)} |")
    return "\n".join(lines)


def flips(before: dict[str, Any], after: dict[str, Any]) -> str:
    out = []
    for kind, key in (("Diagnose", "diag"), ("Remediation", "prop")):
        for c in sorted(after["labels"]):
            b = before[key].get(c)
            a = after[key].get(c)
            b = b["final"] if isinstance(b, dict) else b
            a = a["final"] if isinstance(a, dict) else a
            if b != a:
                out.append(f"- {kind} `{c}`: {'right' if b else 'wrong'} -> {'right' if a else 'wrong'}")
    return "\n".join(out) or "- (none)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--before", type=Path)
    p.add_argument("--before-hand", type=Path)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--after-hand", type=Path)
    args = p.parse_args(argv)
    after = load(args.after, args.after_hand)
    before = load(args.before, args.before_hand) if args.before else None
    print(f"## {after['model']}\n")
    print(table(before, after))
    if before:
        print("\nFlips:\n" + flips(before, after))
    print(f"\nErrors after: {after['errors'] or 'none'}; proposer tool-choice retries: {after['retried'] or 'none'}")
    print(f"Hand-checked diagnoses after: {after['hand_cases']}/{len(after['diag'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
