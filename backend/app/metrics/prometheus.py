"""Query Prometheus for per-pod CPU, memory, restarts, and network series.

Every method degrades cleanly: connection errors, timeouts, and non-success
API responses raise PrometheusUnavailableError so the caller can fall back
instead of crashing a diagnose request.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

import httpx

from app.config import Settings, get_settings
from app.models import ContainerUsage, PodUsage
from ml.features import MetricPoint
from ml.promql import REQUIRED_CHANNELS, pod_queries, usage_queries

logger = logging.getLogger(__name__)

class PrometheusUnavailableError(Exception):
    """Raised when Prometheus cannot be reached or returned an error."""


class PrometheusClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._settings.prometheus_timeout)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    @property
    def base_url(self) -> str:
        return self._settings.prometheus_url.rstrip("/")

    def is_available(self) -> bool:
        try:
            response = self._http().get(f"{self.base_url}/-/ready")
            if response.status_code == 200:
                return True
            # Some images only expose the API, not the ready endpoint.
            response = self._http().get(f"{self.base_url}/api/v1/status/runtimeinfo")
            return response.status_code == 200
        except httpx.HTTPError as exc:
            logger.info("Prometheus is unreachable at %s: %s", self.base_url, exc)
            return False

    def _request(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = self._http().get(url, params=params)
        except httpx.HTTPError as exc:
            raise PrometheusUnavailableError(
                f"Could not reach Prometheus at {self.base_url}: {exc}"
            ) from exc
        if response.status_code >= 500 or response.status_code in {401, 403, 404}:
            raise PrometheusUnavailableError(
                f"Prometheus returned HTTP {response.status_code} for {path}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PrometheusUnavailableError("Prometheus returned a non-JSON body") from exc
        if payload.get("status") != "success":
            error = payload.get("error") or payload.get("status") or "unknown error"
            raise PrometheusUnavailableError(f"Prometheus query failed: {error}")
        return payload.get("data") or {}

    def query(self, expr: str) -> list[dict]:
        data = self._request("/api/v1/query", {"query": expr})
        return list(data.get("result") or [])

    def query_range(self, expr: str, start: float, end: float, step: int) -> list[dict]:
        data = self._request(
            "/api/v1/query_range",
            {"query": expr, "start": start, "end": end, "step": step},
        )
        return list(data.get("result") or [])

    def pod_metric_series(
        self,
        namespace: str,
        name: str,
        window_seconds: int | None = None,
        step_seconds: int | None = None,
    ) -> list[MetricPoint]:
        grouped = self._collect_series(
            namespace=namespace,
            name=name,
            window_seconds=window_seconds,
            step_seconds=step_seconds,
        )
        return grouped.get((namespace, name), [])

    def list_pod_series(
        self,
        namespace: str | None = None,
        window_seconds: int | None = None,
        step_seconds: int | None = None,
    ) -> dict[tuple[str, str], list[MetricPoint]]:
        return self._collect_series(
            namespace=namespace,
            name=None,
            window_seconds=window_seconds,
            step_seconds=step_seconds,
        )

    def _collect_series(
        self,
        namespace: str | None,
        name: str | None,
        window_seconds: int | None,
        step_seconds: int | None,
    ) -> dict[tuple[str, str], list[MetricPoint]]:
        window = window_seconds or self._settings.anomaly_window_seconds
        step = step_seconds or self._settings.anomaly_step_seconds
        end = time.time()
        start = end - window
        queries = pod_queries(namespace, name)

        series_map: dict[tuple[str, str], dict[str, dict[int, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for key, expr in queries.items():
            try:
                results = self.query_range(expr, start, end, step)
            except PrometheusUnavailableError:
                if key in REQUIRED_CHANNELS:
                    raise
                logger.info("Optional Prometheus metric %s is unavailable", key)
                continue
            for series in results:
                metric = series.get("metric") or {}
                ns = metric.get("namespace") or namespace or ""
                pod = metric.get("pod") or name or ""
                if not ns or not pod:
                    continue
                for ts, value in series.get("values") or []:
                    try:
                        series_map[(ns, pod)][key][int(float(ts))] = float(value)
                    except (TypeError, ValueError):
                        continue

        out: dict[tuple[str, str], list[MetricPoint]] = {}
        for pod_key, channels in series_map.items():
            timestamps = sorted({ts for channel in channels.values() for ts in channel})
            if not timestamps:
                continue
            points: list[MetricPoint] = []
            # Network queries already return rates (bytes/sec). Convert them
            # into a cumulative series so extract_features' rate math works.
            rx_cum = 0.0
            tx_cum = 0.0
            prev_ts = timestamps[0]
            for ts in timestamps:
                dt = max(ts - prev_ts, step)
                cpu = channels["cpu"].get(ts)
                mem = channels["memory"].get(ts)
                if cpu is None and mem is None:
                    prev_ts = ts
                    continue
                rx_rate = channels["rx"].get(ts, 0.0)
                tx_rate = channels["tx"].get(ts, 0.0)
                rx_cum += rx_rate * dt
                tx_cum += tx_rate * dt
                points.append(
                    MetricPoint(
                        ts=float(ts),
                        cpu=float(cpu or 0.0),
                        memory_bytes=float(mem or 0.0),
                        memory_limit_bytes=float(channels["limit"].get(ts, 0.0)),
                        restarts=float(channels["restarts"].get(ts, 0.0)),
                        net_rx_bytes=rx_cum,
                        net_tx_bytes=tx_cum,
                    )
                )
                prev_ts = ts
            if points:
                out[pod_key] = points
        return out


    def pod_usage(self, namespace: str, name: str) -> PodUsage:
        """Per-container CPU use, CPU throttling, memory and their limits (instant, 5m windows)."""
        values: dict[str, dict[str, float]] = defaultdict(dict)
        for key, expr in usage_queries(namespace, name).items():
            try:
                results = self.query(expr)
            except PrometheusUnavailableError:
                if key == "cpu_cores":
                    raise
                continue
            for series in results:
                container = (series.get("metric") or {}).get("container")
                try:
                    value = float((series.get("value") or [0, "nan"])[1])
                except (TypeError, ValueError):
                    continue
                if container and value == value:  # skip NaN (0/0 throttling ratio)
                    values[container][key] = value
        containers = []
        for container, v in sorted(values.items()):
            mib = 1024 * 1024
            containers.append(
                ContainerUsage(
                    name=container,
                    cpu_cores=_round(v.get("cpu_cores"), 4),
                    cpu_limit_cores=_round(v.get("cpu_limit_cores"), 4),
                    cpu_request_cores=_round(v.get("cpu_request_cores"), 4),
                    cpu_throttled_ratio=_round(v.get("cpu_throttled_ratio"), 3),
                    memory_mib=_round(v["memory_bytes"] / mib, 1) if "memory_bytes" in v else None,
                    memory_limit_mib=_round(v["memory_limit_bytes"] / mib, 1) if "memory_limit_bytes" in v else None,
                )
            )
        return PodUsage(containers=containers)


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
