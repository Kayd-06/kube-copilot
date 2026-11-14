"""Kubernetes reads and writes for safe actions. Every write supports server-side dry run."""

from __future__ import annotations

import copy
import difflib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from kubernetes import client
from kubernetes.client import ApiException

from app.actions.models import ActionKind, Preview
from app.actions.policy import DeploymentState, PodState, TargetState
from app.k8s_client import K8sClient, K8sUnavailableError, diagnose_reason

logger = logging.getLogger(__name__)

REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"


class ActionExecutionError(Exception):
    pass


def _revision(meta) -> int | None:
    try:
        return int(((meta.annotations or {}) if meta else {}).get(REVISION_ANNOTATION))
    except (TypeError, ValueError):
        return None


def template_summary(template) -> dict[str, Any]:
    """The parts of a pod template a reviewer cares about."""
    if template is None:
        return {}
    meta = template.metadata
    annotations = dict((meta.annotations or {}) if meta else {})
    containers = []
    for c in (template.spec.containers or []) if template.spec else []:
        containers.append(
            {
                "name": c.name,
                "image": c.image,
                "command": c.command,
                "args": c.args,
            }
        )
    return {"annotations": annotations, "containers": containers}


# Fields the API server fills with defaults; they never explain a broken rollout.
_DEFAULT_NOISE = {
    "terminationMessagePath", "terminationMessagePolicy", "dnsPolicy", "schedulerName",
    "securityContext", "restartPolicy", "terminationGracePeriodSeconds", "creationTimestamp",
}
_MAX_DIFF_LINES = 25
_MAX_VALUE_CHARS = 160


def _plain(obj: Any) -> Any:
    """A V1PodTemplateSpec (or already-plain dict) as JSON-style dicts with camelCase keys."""
    if obj is None:
        return None
    if isinstance(obj, (dict, list, str, int, float, bool)):
        return obj
    return client.ApiClient().sanitize_for_serialization(obj)


def _flatten(value: Any, path: str, out: dict[str, Any]) -> None:
    """Flatten into dotted paths. Lists of named objects (containers, env, volumes...) are keyed by name."""
    if isinstance(value, dict):
        if not value:
            return
        for key, sub in value.items():
            if key in _DEFAULT_NOISE and not path.endswith("labels") and not path.endswith("annotations"):
                continue
            if key == "pod-template-hash":
                continue
            _flatten(sub, f"{path}.{key}" if path else key, out)
    elif isinstance(value, list):
        if value and all(isinstance(v, dict) and "name" in v for v in value):
            for item in value:
                rest = {k: v for k, v in item.items() if k != "name"}
                key = f"{path}[{item['name']}]"
                if rest:
                    _flatten(rest, key, out)
                else:
                    out[key] = "(present)"
        elif value and all(isinstance(v, dict) for v in value):
            for i, item in enumerate(value):
                _flatten(item, f"{path}[{i}]", out)
        elif value:
            out[path] = value
    elif value is not None:
        out[path] = value


def _short(value: Any) -> str:
    text = json.dumps(value, default=str, separators=(",", ":")) if not isinstance(value, str) else json.dumps(value)
    return text if len(text) <= _MAX_VALUE_CHARS else text[: _MAX_VALUE_CHARS - 1] + "…"


def template_diff(previous: Any, current: Any) -> list[str]:
    """Compact field-level diff of two pod templates (previous -> current).

    Covers everything in the template: image, command/args, env and envFrom
    (including ConfigMap/Secret refs), probes, resources, nodeSelector,
    affinity, tolerations, volumes and mounts. Lines look like
    `~ spec.containers[web].env[PORT].value: "8080" -> "eighty"`.
    """
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    _flatten(_plain(previous) or {}, "", before)
    _flatten(_plain(current) or {}, "", after)
    lines = []
    for key in sorted(set(before) | set(after)):
        if key.startswith("metadata.annotations.") and key.endswith("restartedAt") and key in before and key in after:
            lines.append(f"~ {key}: (rolling restart)")
            continue
        if key not in before:
            lines.append(f"+ {key}: {_short(after[key])}")
        elif key not in after:
            lines.append(f"- {key}: {_short(before[key])}")
        elif before[key] != after[key]:
            lines.append(f"~ {key}: {_short(before[key])} -> {_short(after[key])}")
    if len(lines) > _MAX_DIFF_LINES:
        extra = len(lines) - _MAX_DIFF_LINES
        lines = lines[:_MAX_DIFF_LINES] + [f"... {extra} more changed field(s)"]
    return lines


def template_refs(template: Any) -> dict[str, list[str]]:
    """ConfigMaps/Secrets/PVCs the template references (env, envFrom, volumes)."""
    flat: dict[str, Any] = {}
    _flatten(_plain(template) or {}, "", flat)
    refs: dict[str, set[str]] = {"configmaps": set(), "secrets": set(), "pvcs": set()}
    for key, value in flat.items():
        if not isinstance(value, str):
            continue
        if key.endswith(("configMapKeyRef.name", "configMapRef.name", "configMap.name")):
            refs["configmaps"].add(value)
        elif key.endswith(("secretKeyRef.name", "secretRef.name", "secret.secretName")):
            refs["secrets"].add(value)
        elif key.endswith("persistentVolumeClaim.claimName"):
            refs["pvcs"].add(value)
    return {k: sorted(v) for k, v in refs.items() if v}


def deployment_summary(dep) -> dict[str, Any]:
    return {
        "replicas": dep.spec.replicas if dep.spec else None,
        "revision": _revision(dep.metadata),
        "template": template_summary(dep.spec.template if dep.spec else None),
    }


def render_diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    a = json.dumps(before, indent=2, sort_keys=True, default=str).splitlines()
    b = json.dumps(after, indent=2, sort_keys=True, default=str).splitlines()
    return "\n".join(difflib.unified_diff(a, b, "live", "after (dry run)", lineterm=""))


def is_crashlooping(pod) -> tuple[bool, int, str | None]:
    restarts = 0
    looping = False
    reason = None
    for cs in (pod.status.container_statuses or []) if pod.status else []:
        restarts = max(restarts, int(cs.restart_count or 0))
        waiting = cs.state.waiting if cs.state is not None else None
        if waiting is not None and waiting.reason == "CrashLoopBackOff":
            looping, reason = True, "CrashLoopBackOff"
        elif not cs.ready and int(cs.restart_count or 0) >= 3:
            looping, reason = True, reason or "repeated restarts"
    return looping, restarts, reason


class ActionsCluster:
    """Reads target state and applies the four allowed actions."""

    def __init__(self, k8s: K8sClient) -> None:
        self._k8s = k8s

    def _apis(self) -> tuple[client.CoreV1Api, client.AppsV1Api]:
        core = self._k8s._ensure_connected()  # noqa: SLF001 — same connection as the read-only client
        apps = self._k8s._apps or client.AppsV1Api()  # noqa: SLF001
        return core, apps

    # ---------- reads ----------
    def _replicasets_for(self, apps: client.AppsV1Api, dep) -> list:
        selector = ",".join(f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items())
        items = apps.list_namespaced_replica_set(dep.metadata.namespace, label_selector=selector).items or []
        uid = dep.metadata.uid
        return [
            rs
            for rs in items
            if any(ref.uid == uid and ref.controller for ref in (rs.metadata.owner_references or []))
        ]

    def _previous_replicaset(self, apps: client.AppsV1Api, dep):
        current = _revision(dep.metadata)
        if current is None:
            return None
        older = [rs for rs in self._replicasets_for(apps, dep) if (_revision(rs.metadata) or 0) < current]
        if not older:
            return None
        return max(older, key=lambda rs: _revision(rs.metadata) or 0)

    def deployment_state(self, namespace: str, name: str) -> DeploymentState | None:
        _, apps = self._apis()
        try:
            dep = apps.read_namespaced_deployment(name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise K8sUnavailableError(f"Could not read Deployment {namespace}/{name}: {exc.reason}") from exc
        prev = self._previous_replicaset(apps, dep)
        status = dep.status
        return DeploymentState(
            namespace=namespace,
            name=name,
            replicas=int(dep.spec.replicas if dep.spec.replicas is not None else 1),
            ready=int(status.ready_replicas or 0) if status else 0,
            available=int(status.available_replicas or 0) if status else 0,
            updated=int(status.updated_replicas or 0) if status else 0,
            generation=int(dep.metadata.generation or 0),
            observed_generation=int(status.observed_generation or 0) if status else 0,
            revision=_revision(dep.metadata),
            previous_revision=_revision(prev.metadata) if prev is not None else None,
            summary=deployment_summary(dep),
            previous_summary=template_summary(prev.spec.template) if prev is not None else None,
            template_diff=(
                template_diff(prev.spec.template, dep.spec.template) if prev is not None and dep.spec else None
            ),
            template_refs=template_refs(dep.spec.template if dep.spec else None),
        )

    def pod_state(self, namespace: str, name: str) -> PodState | None:
        core, apps = self._apis()
        try:
            pod = core.read_namespaced_pod(name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise K8sUnavailableError(f"Could not read pod {namespace}/{name}: {exc.reason}") from exc
        controller = next((ref for ref in (pod.metadata.owner_references or []) if ref.controller), None)
        deployment = None
        if controller is not None and controller.kind == "ReplicaSet":
            try:
                rs = apps.read_namespaced_replica_set(controller.name, namespace)
                owner = next((ref for ref in (rs.metadata.owner_references or []) if ref.controller), None)
                if owner is not None and owner.kind == "Deployment":
                    deployment = owner.name
            except ApiException as exc:
                logger.info("Could not read ReplicaSet %s/%s: %s", namespace, controller.name, exc.reason)
        looping, restarts, reason = is_crashlooping(pod)
        return PodState(
            namespace=namespace,
            name=name,
            controller_kind=controller.kind if controller is not None else None,
            controller_name=controller.name if controller is not None else None,
            deployment=deployment,
            crashlooping=looping,
            restart_count=restarts,
            reason=reason,
        )

    def target_state(self, kind: str, params: dict[str, Any]) -> TargetState:
        namespace, name = params.get("namespace"), params.get("name")
        if not isinstance(namespace, str) or not isinstance(name, str):
            return TargetState()
        if kind == ActionKind.DELETE_POD.value:
            return TargetState(pod=self.pod_state(namespace, name))
        return TargetState(deployment=self.deployment_state(namespace, name))

    def workload_health(
        self, namespace: str, deployment: str
    ) -> tuple[DeploymentState | None, list[str], dict[str, int]]:
        """Deployment status, its unhealthy pods (crashloop, image pull...) and restart counts per pod."""
        core, apps = self._apis()
        state = self.deployment_state(namespace, deployment)
        if state is None:
            return None, [], {}
        dep = apps.read_namespaced_deployment(deployment, namespace)
        selector = ",".join(f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items())
        pods = core.list_namespaced_pod(namespace, label_selector=selector).items or []
        unhealthy = []
        restarts: dict[str, int] = {}
        for pod in pods:
            if pod.metadata.deletion_timestamp is not None:
                continue  # terminating pods from the old ReplicaSet
            bad, reason, _ = diagnose_reason(pod)
            looping, count, _ = is_crashlooping(pod)
            restarts[pod.metadata.name] = count
            if bad or looping:
                unhealthy.append(f"{pod.metadata.name} ({reason or 'crashlooping'})")
        return state, unhealthy, restarts

    # ---------- writes ----------
    def apply(self, kind: str, params: dict[str, Any], dry_run: bool) -> tuple[dict, dict]:
        """Run one allowed action. Returns (before, after) summaries. Raises ActionExecutionError."""
        core, apps = self._apis()
        namespace, name = params["namespace"], params["name"]
        dry = "All" if dry_run else None
        try:
            if kind == ActionKind.DELETE_POD.value:
                pod = core.read_namespaced_pod(name, namespace)
                before = {"pod": name, "phase": pod.status.phase if pod.status else None, "exists": True}
                core.delete_namespaced_pod(name, namespace, dry_run=dry)
                owner = next((ref.name for ref in (pod.metadata.owner_references or []) if ref.controller), None)
                after = {"pod": name, "exists": False, "recreated_by": f"ReplicaSet/{owner}"}
                return before, after

            dep = apps.read_namespaced_deployment(name, namespace)
            before = deployment_summary(dep)
            if kind == ActionKind.RESTART_DEPLOYMENT.value:
                stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                body = {"spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: stamp}}}}}
                result = apps.patch_namespaced_deployment(name, namespace, body, dry_run=dry)
                return before, deployment_summary(result)
            if kind == ActionKind.SCALE_DEPLOYMENT.value:
                body = {"spec": {"replicas": int(params["replicas"])}}
                result = apps.patch_namespaced_deployment_scale(name, namespace, body, dry_run=dry)
                after = copy.deepcopy(before)
                after["replicas"] = result.spec.replicas
                return before, after
            if kind == ActionKind.ROLLBACK_DEPLOYMENT.value:
                prev = self._previous_replicaset(apps, dep)
                if prev is None:
                    raise ActionExecutionError("no previous revision to roll back to")
                template = apps.api_client.sanitize_for_serialization(prev.spec.template)
                labels = template.get("metadata", {}).get("labels", {})
                labels.pop("pod-template-hash", None)
                # Same as `kubectl rollout undo`: put the previous template back.
                patch = [{"op": "replace", "path": "/spec/template", "value": template}]
                result = apps.patch_namespaced_deployment(name, namespace, patch, dry_run=dry)
                after = deployment_summary(result)
                if dry_run:
                    # A dry run does not bump the revision; show which one we go back to.
                    after["revision"] = f"new revision with the template of revision {_revision(prev.metadata)}"
                return before, after
        except ApiException as exc:
            detail = exc.reason
            try:
                detail = json.loads(exc.body).get("message") or detail
            except (TypeError, ValueError, AttributeError):
                pass
            raise ActionExecutionError(f"Kubernetes API refused the {kind}: {detail}") from exc
        raise ActionExecutionError(f"unsupported action {kind}")

    def preview(self, kind: str, params: dict[str, Any]) -> Preview:
        try:
            before, after = self.apply(kind, params, dry_run=True)
        except ActionExecutionError as exc:
            return Preview(dry_run_ok=False, dry_run_error=str(exc))
        return Preview(dry_run_ok=True, before=before, after=after, diff=render_diff(before, after))
