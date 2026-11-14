"""Load frozen eval fixtures into the API's own types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.actions.policy import DeploymentState, PodState
from app.models import AnomalyScore, PodEvent, PodLogs, PodSummary
from app.rag.retriever import RetrievedChunk

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "llm_eval" / "cases"


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
    )


def load_all(directory: Path = FIXTURE_DIR, only: set[str] | None = None) -> list[Fixture]:
    out = [load_fixture(p) for p in sorted(directory.glob("*.json"))]
    return [f for f in out if not only or f.id in only]
