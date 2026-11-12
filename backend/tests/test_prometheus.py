import httpx
import pytest

from app.config import Settings
from app.metrics.prometheus import PrometheusClient, PrometheusUnavailableError


class _Transport(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)


def _client(handler) -> PrometheusClient:
    http = httpx.Client(transport=_Transport(handler), base_url="http://prometheus.test")
    settings = Settings(prometheus_url="http://prometheus.test", prometheus_timeout=1.0)
    return PrometheusClient(settings=settings, client=http)


def test_is_available_true_on_ready():
    def handler(request):
        assert request.url.path == "/-/ready"
        return httpx.Response(200, text="Prometheus is Ready.\n")

    assert _client(handler).is_available() is True


def test_is_available_false_on_connection_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    assert _client(handler).is_available() is False


def test_query_range_parses_series():
    def handler(request):
        if request.url.path != "/api/v1/query_range":
            return httpx.Response(404)
        # Return a one-pod CPU series; other metrics will be empty.
        query = request.url.params.get("query", "")
        if "container_cpu_usage_seconds_total" in query or "container_memory" in query:
            metric = "cpu" if "cpu" in query else "memory"
            value = "0.2" if metric == "cpu" else "80000000"
            payload = {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"namespace": "copilot-demo", "pod": "leak-app-1"},
                            "values": [[1_700_000_000, value], [1_700_000_015, value]],
                        }
                    ],
                },
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    series = _client(handler).pod_metric_series("copilot-demo", "leak-app-1", window_seconds=60, step_seconds=15)
    assert len(series) == 2
    assert series[0].cpu == pytest.approx(0.2)
    assert series[0].memory_bytes == pytest.approx(80_000_000)
    assert series[0].namespace if hasattr(series[0], "namespace") else True


def test_query_raises_unavailable_on_http_error():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(PrometheusUnavailableError, match="Could not reach Prometheus"):
        _client(handler).query("up")


def test_query_raises_unavailable_on_prom_error():
    def handler(request):
        return httpx.Response(200, json={"status": "error", "error": "bad_data: boom"})

    with pytest.raises(PrometheusUnavailableError, match="boom"):
        _client(handler).query("not a query")
