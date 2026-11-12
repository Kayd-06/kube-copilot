"""Optional background poller that pre-scores pods on a timer.

Disabled by default (ANOMALY_POLLER_ENABLED=false) so a low-spec laptop is
not doing extra Prometheus queries unless the operator turns it on.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.anomaly.scorer import AnomalyModelMissingError, AnomalyScorer, ScoreResult
from app.config import Settings, get_settings
from app.metrics.instrumentation import record_anomaly
from app.metrics.prometheus import PrometheusClient, PrometheusUnavailableError

logger = logging.getLogger(__name__)


class AnomalyPoller:
    def __init__(
        self,
        scorer: AnomalyScorer,
        prometheus: PrometheusClient,
        settings: Settings | None = None,
    ) -> None:
        self._scorer = scorer
        self._prometheus = prometheus
        self._settings = settings or get_settings()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], ScoreResult] = {}
        self._last_error: str | None = None
        self._last_run: datetime | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_run(self) -> datetime | None:
        return self._last_run

    def snapshot(self) -> list[ScoreResult]:
        with self._lock:
            return list(self._cache.values())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="anomaly-poller", daemon=True)
        self._thread.start()
        logger.info(
            "Anomaly poller started (interval=%ss)",
            self._settings.anomaly_poll_interval_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)

    def _loop(self) -> None:
        interval = max(int(self._settings.anomaly_poll_interval_seconds), 5)
        # Run once immediately so the cache is warm.
        self._tick()
        while not self._stop.wait(interval):
            self._tick()

    def _tick(self) -> None:
        try:
            series = self._prometheus.list_pod_series()
        except PrometheusUnavailableError as exc:
            self._last_error = str(exc)
            logger.info("Anomaly poller skipped a tick: %s", exc)
            return
        results: dict[tuple[str, str], ScoreResult] = {}
        try:
            for (namespace, name), points in series.items():
                try:
                    results[(namespace, name)] = self._scorer.score_window(namespace, name, points)
                except AnomalyModelMissingError as exc:
                    self._last_error = str(exc)
                    logger.info("Anomaly poller stopped: %s", exc)
                    return
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("Anomaly poller failed: %s", exc)
            return
        with self._lock:
            self._cache = results
            self._last_error = None
            self._last_run = datetime.now(timezone.utc)
        for item in results.values():
            record_anomaly(item.namespace, item.name, item.score, item.is_anomaly)
        flagged = sum(1 for item in results.values() if item.is_anomaly)
        logger.info("Anomaly poller scored %s pods (%s flagged)", len(results), flagged)
