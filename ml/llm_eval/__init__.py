"""LLM evaluation harness for Diagnose and the safe-actions proposer.

- `cases.py`     hand-labelled fault scenarios reproduced on the kind cluster
- `capture.py`   runs the scenarios and freezes what Diagnose / the proposer see
- `taxonomy.py`  root-cause categories and the deterministic matchers
- `scoring.py`   schema, category, remediation, retrieval and hallucination checks
- `run.py`       `make eval`: replays the frozen fixtures against a model
- `judge.py`     optional LLM judge with a fixed rubric
"""
