"""Load frozen eval fixtures into the API's own types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.actions.policy import DeploymentState, PodState
from app.models import AnomalyScore, DependencyStatus, PodEvent, PodLogs, PodSummary, PodUsage
from app.rag.retriever import RetrievedChunk

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "llm_eval"
# v1: the 38 incidents frozen for PR #31 (kept unchanged for before/after comparisons).
FIXTURE_DIR_V1 = DATA_DIR / "cases"
# v2: v1 with the cases that needed new context fields (template diff, CPU throttling) re-captured.
FIXTURE_DIR = DATA_DIR / "cases_v2"
# Held-out incidents captured after the prompt changes; never used to tune prompts.
HELDOUT_DIR = DATA_DIR / "heldout"
# v3 (schema 3): adds the dependency check; retrieval re-run against the current runbooks (see README).
FIXTURE_DIR_V3 = DATA_DIR / "cases_v3"
HELDOUT_DIR_V3 = DATA_DIR / "heldout_v3"


@dataclass
class Fixture:
    raw: dict[str, Any]
    pod: PodSummary
    events: list[PodEvent]
    logs: PodLogs
    retrieved: list[RetrievedChunk]
    anomaly: AnomalyScore | None
    deployment: DeploymentState | None
    pod_state: PodState | None
    signals: list[str]
    usage: PodUsage | None = None
    dependencies: list[DependencyStatus] | None = None

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def labels(self) -> dict[str, Any]:
        return self.raw["labels"]

    @property
    def namespace(self) -> str:
        return self.raw["namespace"]

    @property
    def has_signals(self) -> bool:
        return bool(self.signals) or bool(self.anomaly and self.anomaly.lifecycle_signals)


def load_fixture(path: Path) -> Fixture:
    raw = json.loads(path.read_text())
    di, pi = raw["diagnose_input"], raw["propose_input"]
    dep = pi.get("deployment")
    ps = pi.get("pod_state")
    return Fixture(
        raw=raw,
        pod=PodSummary(**di["pod"]),
        events=[PodEvent(**e) for e in di["events"]],
        logs=PodLogs(**di["logs"]),
        retrieved=[RetrievedChunk(**c) for c in di["retrieved"]],
        anomaly=AnomalyScore(**di["anomaly"]) if di.get("anomaly") else None,
        deployment=DeploymentState(**dep) if dep else None,
        pod_state=PodState(**ps) if ps else None,
        signals=list(pi.get("signals") or []),
        usage=PodUsage(**di["usage"]) if di.get("usage") else None,
        dependencies=[DependencyStatus(**d) for d in di["dependencies"]] if di.get("dependencies") else None,
    )


def load_all(directory: Path = FIXTURE_DIR_V3, only: set[str] | None = None) -> list[Fixture]:
    out = [load_fixture(p) for p in sorted(directory.glob("*.json"))]
    return [f for f in out if not only or f.id in only]
