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


def lifecycle_queries(namespace: str | None = None) -> dict[str, str]:
    """kube-state-metrics series for workload / pod lifecycle (ml/lifecycle.py).

    Keys starting with `deploy_` are grouped by (namespace, deployment); the
    rest by (namespace, pod) plus the extra label named in the key's comment.
    """
    ns = f'namespace="{namespace}"' if namespace else ""
    return {
        "deploy_desired": f"sum by (namespace, deployment) (kube_deployment_spec_replicas{{{ns}}})",
        "deploy_available": f"sum by (namespace, deployment) (kube_deployment_status_replicas_available{{{ns}}})",
        "deploy_generation": f"max by (namespace, deployment) (kube_deployment_metadata_generation{{{ns}}})",
        # value = pod creation time (unix seconds); a new pod name with a fresh
        # value is a replacement, a series that stops is a pod that went away.
        "pod_created": f"max by (namespace, pod) (kube_pod_created{{{ns}}})",
        # extra label: owner_name (the ReplicaSet for Deployment pods)
        "pod_owner": f'max by (namespace, pod, owner_kind, owner_name) (kube_pod_owner{{{ns}}})',
        # extra label: reason (OOMKilled, Error, Completed, ...)
        "pod_last_terminated": (
            f"max by (namespace, pod, reason) (kube_pod_container_status_last_terminated_reason{{{ns}}})"
        ),
        "pod_ready": f'min by (namespace, pod) (kube_pod_status_ready{{{ns}{"," if ns else ""}condition="true"}})',
    }
