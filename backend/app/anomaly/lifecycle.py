"""Live pod-disruption signals from the Kubernetes API (rules in ml/lifecycle.py).

The Isolation Forest only sees pods that still report metrics. A killed pod
just disappears, so this reads events, pod ages, Deployment replica counts and
recent container restarts instead, and groups what it finds by workload.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import Settings, get_settings
from ml.lifecycle import DetectorConfig, Signal, SignalIndex, detect

logger = logging.getLogger(__name__)


@dataclass
class LifecycleReport:
    signals: list[Signal] = field(default_factory=list)
    pod_workloads: dict[tuple[str, str], str] = field(default_factory=dict)
    lookback_s: float = 180.0

    def __post_init__(self) -> None:
        self._index = SignalIndex(self.signals, self.pod_workloads)

    def for_pod(self, namespace: str, pod: str, now: float | None = None) -> list[Signal]:
        now = now if now is not None else datetime.now(timezone.utc).timestamp()
        return self._index.for_window(namespace, pod, now - self.lookback_s, now, slack=0.0)

    def by_workload(self) -> dict[tuple[str, str], list[Signal]]:
        out: dict[tuple[str, str], list[Signal]] = defaultdict(list)
        for signal in self.signals:
            out[(signal.namespace, signal.workload)].append(signal)
        return dict(out)


class LifecycleDetector:
    def __init__(self, k8s, settings: Settings | None = None) -> None:
        self._k8s = k8s
        self._settings = settings or get_settings()

    @property
    def lookback_s(self) -> float:
        return float(self._settings.anomaly_window_seconds)

    def snapshot(self, namespace: str | None = None, now: float | None = None) -> LifecycleReport:
        """Raise K8sUnavailableError when the API cannot be read; callers degrade."""
        now = now if now is not None else datetime.now(timezone.utc).timestamp()
        lookback = self.lookback_s
        events, pods, workloads, restarts = self._k8s.lifecycle_snapshot(namespace=namespace, lookback_s=lookback, now=now)
        # Live: no history of pods that already went away, so no pod_vanished;
        # Killing / SuccessfulDelete / SuccessfulCreate events cover those.
        config = DetectorConfig(observation_start=now - lookback, observation_end=None)
        signals = [s for s in detect(events, pods, workloads, restarts, config) if s.ts >= now - lookback]
        return LifecycleReport(
            signals=signals,
            pod_workloads={(p.namespace, p.pod): p.workload for p in pods},
            lookback_s=lookback,
        )
