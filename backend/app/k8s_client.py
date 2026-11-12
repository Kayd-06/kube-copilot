"""Kubernetes client for listing unhealthy pods and fetching logs plus events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.client.models import (
    V1ContainerState,
    V1ContainerStatus,
    V1Pod,
)

from app.config import Settings, get_settings
from app.models import ContainerStateSummary, PodEvent, PodLogs, PodSummary

logger = logging.getLogger(__name__)

# Waiting / terminated reasons that indicate a pod needs attention.
UNHEALTHY_REASONS = frozenset(
    {
        "CrashLoopBackOff",
        "Error",
        "ImagePullBackOff",
        "ErrImagePull",
        "OOMKilled",
        "CreateContainerConfigError",
        "CreateContainerError",
        "InvalidImageName",
        "RunContainerError",
        "DeadlineExceeded",
        "Evicted",
        "StartError",
        "ContainerCannotRun",
    }
)

UNHEALTHY_PHASES = frozenset({"Failed", "Unknown"})


class K8sUnavailableError(Exception):
    """Raised when the Kubernetes API cannot be reached or configured."""


class PodNotFoundError(Exception):
    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        super().__init__(f"Pod {namespace}/{name} was not found")


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _state_fields(state: V1ContainerState | None) -> tuple[str, str | None, str | None, int | None]:
    """Return (state_name, reason, message, exit_code) from a container state."""
    if state is None:
        return "unknown", None, None, None
    if state.waiting is not None:
        return "waiting", state.waiting.reason, state.waiting.message, None
    if state.terminated is not None:
        return (
            "terminated",
            state.terminated.reason,
            state.terminated.message,
            state.terminated.exit_code,
        )
    if state.running is not None:
        return "running", None, None, None
    return "unknown", None, None, None


def summarize_container(status: V1ContainerStatus) -> ContainerStateSummary:
    state_name, reason, message, exit_code = _state_fields(status.state)
    # Prefer a more specific last-terminated reason (e.g. OOMKilled) when
    # the current state is a generic CrashLoopBackOff wait.
    if reason in {None, "CrashLoopBackOff", "Error"} and status.last_state is not None:
        _, last_reason, last_message, last_exit = _state_fields(status.last_state)
        if last_reason:
            if reason in {None, "CrashLoopBackOff", "Error"}:
                # Keep waiting reason as the state, but surface the terminated reason
                # when it is more specific (OOMKilled, Error with exit code).
                if last_reason not in {None, "Error", "Completed"} or reason is None:
                    if last_reason in UNHEALTHY_REASONS and last_reason != reason:
                        reason = last_reason if reason is None else reason
                        message = message or last_message
            if exit_code is None:
                exit_code = last_exit
    return ContainerStateSummary(
        name=status.name,
        ready=bool(status.ready),
        restart_count=int(status.restart_count or 0),
        state=state_name,
        reason=reason,
        message=message,
        exit_code=exit_code,
    )


def diagnose_reason(pod: V1Pod) -> tuple[bool, str | None, str | None]:
    """Decide whether a pod is unhealthy and return (unhealthy, reason, message)."""
    status = pod.status
    if status is None:
        return False, None, None

    phase = status.phase or "Unknown"
    pod_reason = status.reason
    pod_message = status.message

    if phase in UNHEALTHY_PHASES:
        return True, pod_reason or phase, pod_message

    container_reasons: list[str] = []
    container_messages: list[str] = []
    restart_total = 0
    any_not_ready = False

    for cs in status.container_statuses or []:
        restart_total += int(cs.restart_count or 0)
        summary = summarize_container(cs)
        if not cs.ready:
            any_not_ready = True
        for candidate in (summary.reason,):
            if candidate in UNHEALTHY_REASONS:
                container_reasons.append(candidate)
                if summary.message:
                    container_messages.append(summary.message)
        # Also inspect last terminated state directly for OOMKilled / Error.
        if cs.last_state is not None:
            _, last_reason, last_message, _ = _state_fields(cs.last_state)
            if last_reason in UNHEALTHY_REASONS:
                container_reasons.append(last_reason)
                if last_message:
                    container_messages.append(last_message)

    if container_reasons:
        # Prefer specific reasons over generic CrashLoopBackOff when both exist.
        priority = [
            "OOMKilled",
            "ImagePullBackOff",
            "ErrImagePull",
            "InvalidImageName",
            "CreateContainerConfigError",
            "CreateContainerError",
            "CrashLoopBackOff",
            "Error",
        ]
        chosen = next((r for r in priority if r in container_reasons), container_reasons[0])
        message = next(iter(container_messages), pod_message)
        return True, chosen, message

    if phase == "Pending":
        # Check pod-level conditions like Unschedulable, plus init/container waits.
        condition_reason = pod_reason
        condition_message = pod_message
        for cond in status.conditions or []:
            if cond.type == "PodScheduled" and cond.status == "False":
                condition_reason = cond.reason or "Pending"
                condition_message = cond.message or condition_message
                break
        for ics in status.init_container_statuses or []:
            _, reason, message, _ = _state_fields(ics.state)
            if reason:
                return True, reason, message
        waiting_reasons = []
        for cs in status.container_statuses or []:
            _, reason, message, _ = _state_fields(cs.state)
            if reason:
                waiting_reasons.append((reason, message))
        if waiting_reasons:
            return True, waiting_reasons[0][0], waiting_reasons[0][1]
        return True, condition_reason or "Pending", condition_message

    # Not-ready with restarts is almost always a crash loop the API has not
    # yet labeled, or a failing readiness probe.
    if any_not_ready and restart_total > 0 and phase != "Succeeded":
        return True, pod_reason or "Error", pod_message

    return False, pod_reason, pod_message


def to_pod_summary(pod: V1Pod) -> PodSummary:
    metadata = pod.metadata
    status = pod.status
    containers = [summarize_container(cs) for cs in (status.container_statuses or [])] if status else []
    unhealthy, reason, message = diagnose_reason(pod)
    restart_count = sum(c.restart_count for c in containers)
    ready = all(c.ready for c in containers) if containers else False
    # If diagnose_reason found nothing wrong, still fill reason from the first container.
    if reason is None and containers:
        reason = next((c.reason for c in containers if c.reason), None)
        message = message or next((c.message for c in containers if c.message), None)
    _ = unhealthy  # used by callers via diagnose_reason directly
    return PodSummary(
        name=metadata.name if metadata else "unknown",
        namespace=metadata.namespace if metadata else "default",
        phase=(status.phase if status and status.phase else "Unknown"),
        reason=reason,
        message=message,
        restart_count=restart_count,
        ready=ready,
        created_at=_utc(metadata.creation_timestamp) if metadata else None,
        node=(pod.spec.node_name if pod.spec else None),
        qos_class=status.qos_class if status else None,
        containers=containers,
    )


class K8sClient:
    """Thin wrapper around the official Kubernetes Python client."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._loaded = False
        self._core: client.CoreV1Api | None = None

    def _ensure_connected(self) -> client.CoreV1Api:
        if self._core is not None and self._loaded:
            return self._core
        try:
            if self._settings.k8s_in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(config_file=self._settings.k8s_kubeconfig)
            self._core = client.CoreV1Api()
            self._loaded = True
            return self._core
        except Exception as exc:  # noqa: BLE001 — surface any kubeconfig / auth failure
            raise K8sUnavailableError(
                "Could not load Kubernetes configuration. "
                "Set K8S_KUBECONFIG, start a kind cluster (make cluster-up), "
                "or set K8S_IN_CLUSTER=true when running inside a cluster."
            ) from exc

    def list_unhealthy_pods(self, namespace: str | None = None) -> list[PodSummary]:
        api = self._ensure_connected()
        try:
            if namespace:
                pod_list = api.list_namespaced_pod(namespace=namespace)
            else:
                pod_list = api.list_pod_for_all_namespaces()
        except ApiException as exc:
            raise K8sUnavailableError(f"Kubernetes API error while listing pods: {exc.reason}") from exc

        unhealthy: list[PodSummary] = []
        for pod in pod_list.items or []:
            is_bad, _, _ = diagnose_reason(pod)
            if is_bad:
                unhealthy.append(to_pod_summary(pod))
        unhealthy.sort(key=lambda p: (p.namespace, p.name))
        return unhealthy

    def get_pod(self, namespace: str, name: str) -> V1Pod:
        api = self._ensure_connected()
        try:
            return api.read_namespaced_pod(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise PodNotFoundError(namespace, name) from exc
            raise K8sUnavailableError(
                f"Kubernetes API error while reading pod {namespace}/{name}: {exc.reason}"
            ) from exc

    def get_pod_logs(self, namespace: str, name: str, pod: V1Pod | None = None) -> PodLogs:
        api = self._ensure_connected()
        if pod is None:
            pod = self.get_pod(namespace, name)

        container_name = None
        restart_count = 0
        statuses = (pod.status.container_statuses or []) if pod.status else []
        if statuses:
            # Prefer the container that looks unhealthy.
            chosen = statuses[0]
            for cs in statuses:
                summary = summarize_container(cs)
                if summary.reason in UNHEALTHY_REASONS or not cs.ready:
                    chosen = cs
                    break
            container_name = chosen.name
            restart_count = int(chosen.restart_count or 0)

        tail = self._settings.log_tail_lines
        current = self._read_logs(api, namespace, name, container_name, previous=False, tail=tail)
        previous = None
        if restart_count > 0:
            previous = self._read_logs(api, namespace, name, container_name, previous=True, tail=tail)
        return PodLogs(current=current, previous=previous, container=container_name)

    def _read_logs(
        self,
        api: client.CoreV1Api,
        namespace: str,
        name: str,
        container: str | None,
        previous: bool,
        tail: int,
    ) -> str:
        kwargs: dict = {
            "name": name,
            "namespace": namespace,
            "tail_lines": tail,
            "timestamps": True,
            "previous": previous,
        }
        if container:
            kwargs["container"] = container
        try:
            logs = api.read_namespaced_pod_log(**kwargs)
            return logs if isinstance(logs, str) else (logs or "")
        except ApiException as exc:
            logger.info(
                "Could not read %s logs for %s/%s: %s",
                "previous" if previous else "current",
                namespace,
                name,
                exc.reason,
            )
            return f"(logs unavailable: {exc.reason or 'unknown error'})"
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not read logs for %s/%s: %s", namespace, name, exc)
            return f"(logs unavailable: {exc})"

    def get_pod_events(self, namespace: str, name: str) -> list[PodEvent]:
        api = self._ensure_connected()
        field_selector = f"involvedObject.name={name},involvedObject.kind=Pod"
        try:
            event_list = api.list_namespaced_event(namespace=namespace, field_selector=field_selector)
        except ApiException as exc:
            logger.info("Could not list events for %s/%s: %s", namespace, name, exc.reason)
            return []

        events: list[PodEvent] = []
        for ev in event_list.items or []:
            source = None
            if ev.source is not None:
                parts = [p for p in (ev.source.component, ev.source.host) if p]
                source = "/".join(parts) if parts else None
            # Prefer event_time / last_timestamp / metadata.creation_timestamp.
            last_ts = ev.last_timestamp or ev.event_time or (
                ev.metadata.creation_timestamp if ev.metadata else None
            )
            first_ts = ev.first_timestamp or last_ts
            events.append(
                PodEvent(
                    type=ev.type or "Normal",
                    reason=ev.reason or "",
                    message=ev.message or "",
                    count=int(ev.count or 1),
                    first_timestamp=_utc(first_ts),
                    last_timestamp=_utc(last_ts),
                    source=source,
                )
            )
        events.sort(key=lambda e: e.last_timestamp or datetime.min.replace(tzinfo=timezone.utc))
        return events
