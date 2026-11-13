"""Kubernetes client for listing unhealthy pods and fetching logs plus events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.client.models import (
    V1ContainerState,
    V1ContainerStatus,
    V1Pod,
)

from app.config import Settings, get_settings
from app.models import ContainerStateSummary, PodEvent, PodLogs, PodSummary
from ml.lifecycle import LifecycleEvent, PodLife, RestartObservation, WorkloadSample, workload_of

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
        self._apps: client.AppsV1Api | None = None


    def _should_load_incluster(self) -> bool:
        """Prefer in-cluster config when running as a pod, even if the env flag was omitted."""
        if self._settings.k8s_in_cluster:
            return True
        if self._settings.k8s_kubeconfig:
            return False
        return Path("/var/run/secrets/kubernetes.io/serviceaccount/token").is_file()

    def _ensure_connected(self) -> client.CoreV1Api:
        if self._core is not None and self._loaded:
            return self._core
        try:
            if self._should_load_incluster():
                config.load_incluster_config()
            else:
                config.load_kube_config(config_file=self._settings.k8s_kubeconfig)
            self._core = client.CoreV1Api()
            self._apps = client.AppsV1Api()
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

    def lifecycle_snapshot(
        self, namespace: str | None = None, lookback_s: float = 180.0, now: float | None = None
    ) -> tuple[list[LifecycleEvent], list[PodLife], list[WorkloadSample], list[RestartObservation]]:
        """Events, pods, Deployments and recent restarts for ml.lifecycle.detect."""
        core = self._ensure_connected()
        apps = self._apps or client.AppsV1Api()
        try:
            if namespace:
                events = core.list_namespaced_event(namespace=namespace).items or []
                pods = core.list_namespaced_pod(namespace=namespace).items or []
            else:
                events = core.list_event_for_all_namespaces().items or []
                pods = core.list_pod_for_all_namespaces().items or []
        except ApiException as exc:
            raise K8sUnavailableError(f"Kubernetes API error while reading lifecycle state: {exc.reason}") from exc
        # Optional: older RBAC (pods + events only) still gets event and restart signals.
        try:
            if namespace:
                deployments = apps.list_namespaced_deployment(namespace=namespace).items or []
                replicasets = apps.list_namespaced_replica_set(namespace=namespace).items or []
            else:
                deployments = apps.list_deployment_for_all_namespaces().items or []
                replicasets = apps.list_replica_set_for_all_namespaces().items or []
        except ApiException as exc:
            logger.info("Deployments/ReplicaSets unavailable for lifecycle signals: %s", exc.reason)
            deployments, replicasets = [], []
        now = now if now is not None else datetime.now(timezone.utc).timestamp()
        return lifecycle_from_objects(events, pods, deployments, replicasets, now, lookback_s)


def _ts(dt: datetime | None) -> float | None:
    dt = _utc(dt)
    return dt.timestamp() if dt is not None else None


# Chaos Mesh reports its own injections as events; never use them as evidence.
_CHAOS_MESH_KINDS = frozenset(
    {"PodChaos", "StressChaos", "NetworkChaos", "PodNetworkChaos", "Schedule", "IOChaos", "TimeChaos", "HTTPChaos"}
)


def lifecycle_from_objects(
    events: list,
    pods: list[V1Pod],
    deployments: list,
    replicasets: list,
    now: float,
    lookback_s: float,
) -> tuple[list[LifecycleEvent], list[PodLife], list[WorkloadSample], list[RestartObservation]]:
    """Convert Kubernetes API objects into ml.lifecycle inputs (pure, unit-tested)."""
    since = now - lookback_s
    out_events: list[LifecycleEvent] = []
    for ev in events:
        obj = ev.involved_object
        if obj is None or not obj.name or (obj.kind or "") in _CHAOS_MESH_KINDS:
            continue
        last = _ts(ev.last_timestamp) or _ts(ev.event_time) or _ts(ev.metadata.creation_timestamp if ev.metadata else None)
        if last is None or last < since:
            continue
        out_events.append(
            LifecycleEvent(
                ts=last,
                first_ts=_ts(ev.first_timestamp) or last,
                namespace=obj.namespace or (ev.metadata.namespace if ev.metadata else "") or "",
                kind=obj.kind or "",
                name=obj.name,
                reason=ev.reason or "",
                type=ev.type or "Normal",
                message=(ev.message or "")[:300],
                count=int(ev.count or 1),
            )
        )

    rs_created = {
        (rs.metadata.namespace, rs.metadata.name): _ts(rs.metadata.creation_timestamp)
        for rs in replicasets
        if rs.metadata is not None
    }
    out_pods: list[PodLife] = []
    restarts: list[RestartObservation] = []
    for pod in pods:
        meta = pod.metadata
        if meta is None or meta.creation_timestamp is None:
            continue
        owner = next(iter(meta.owner_references or []), None)
        owner_name = owner.name if owner is not None else None
        owner_kind = owner.kind if owner is not None else None
        workload = workload_of(meta.name, owner_name, owner_kind)
        created = _ts(meta.creation_timestamp) or now
        out_pods.append(
            PodLife(
                namespace=meta.namespace,
                pod=meta.name,
                workload=workload,
                created_ts=created,
                first_seen=created,
                last_seen=now,
                owner_created_ts=rs_created.get((meta.namespace, owner_name)) if owner_kind == "ReplicaSet" else None,
            )
        )
        for cs in (pod.status.container_statuses or []) if pod.status else []:
            terminated = cs.last_state.terminated if cs.last_state is not None else None
            finished = _ts(terminated.finished_at) if terminated is not None else None
            if finished is not None and finished >= since and int(cs.restart_count or 0) > 0:
                restarts.append(
                    RestartObservation(
                        ts=finished,
                        namespace=meta.namespace,
                        pod=meta.name,
                        workload=workload,
                        delta=1.0,
                        reason=terminated.reason or "",
                    )
                )

    samples = []
    for dep in deployments:
        if dep.metadata is None:
            continue
        desired = float((dep.spec.replicas if dep.spec and dep.spec.replicas is not None else 1))
        available = float((dep.status.available_replicas or 0) if dep.status else 0)
        samples.append(
            WorkloadSample(
                ts=now,
                namespace=dep.metadata.namespace,
                workload=dep.metadata.name,
                desired=desired,
                available=available,
                generation=float(dep.metadata.generation or 0),
            )
        )
    return out_events, out_pods, samples, restarts
