"""PromQL used to build per-pod metric series.

Shared by the live API (backend/app/metrics/prometheus.py) and the offline
data collector (ml/collect.py) so training data and live scoring read exactly
the same signals.
"""

from __future__ import annotations

# Exclude the pause/POD sandbox and empty container labels.
_CONTAINER = 'container!="", container!="POD"'


def pod_selector(namespace: str | None, name: str | None) -> str:
    """cAdvisor selector (container-level series, summed per pod)."""
    parts = [_CONTAINER]
    if namespace:
        parts.append(f'namespace="{namespace}"')
    if name:
        parts.append(f'pod="{name}"')
    return ",".join(parts)


def namespace_pod_only(namespace: str | None, name: str | None) -> str:
    """Label selector without the cAdvisor container filter (kube-state-metrics / net)."""
    parts = []
    if namespace:
        parts.append(f'namespace="{namespace}"')
    if name:
        parts.append(f'pod="{name}"')
    joined = ",".join(parts)
    return f"{joined}," if joined else ""


def pod_queries(namespace: str | None = None, name: str | None = None) -> dict[str, str]:
    """Return channel -> PromQL. cpu, rx and tx are already per-second rates."""
    selector = pod_selector(namespace, name)
    plain = namespace_pod_only(namespace, name)
    # rate() needs a look-behind window; 2m is short enough for a 15s scrape.
    return {
        "cpu": f"sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{{{selector}}}[2m]))",
        "memory": f"sum by (namespace, pod) (container_memory_working_set_bytes{{{selector}}})",
        "restarts": f"sum by (namespace, pod) (kube_pod_container_status_restarts_total{{{plain}}})",
        "limit": (
            "sum by (namespace, pod) ("
            f'kube_pod_container_resource_limits{{{plain}resource="memory"}})'
        ),
        "rx": f"sum by (namespace, pod) (rate(container_network_receive_bytes_total{{{plain}}}[2m]))",
        "tx": f"sum by (namespace, pod) (rate(container_network_transmit_bytes_total{{{plain}}}[2m]))",
    }


# cpu and memory are required; the rest degrade to zeros when missing.
REQUIRED_CHANNELS = ("cpu", "memory")
