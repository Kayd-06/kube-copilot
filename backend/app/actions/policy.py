"""Policy checks for proposed actions. Pure code: nothing here trusts the LLM.

Every proposal is validated here before a dry run, and again right before
execution against fresh cluster state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.actions.models import ALLOWED_KINDS, ActionKind, ActionRequest
from app.config import Settings

# Refused even if someone lists it as allowed.
HARD_DENIED_NAMESPACES = frozenset({"kube-system"})

_DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")

# Exact parameter sets per action. Anything extra is refused.
PARAMS: dict[str, frozenset[str]] = {
    ActionKind.RESTART_DEPLOYMENT.value: frozenset({"namespace", "name"}),
    ActionKind.SCALE_DEPLOYMENT.value: frozenset({"namespace", "name", "replicas"}),
    ActionKind.ROLLBACK_DEPLOYMENT.value: frozenset({"namespace", "name"}),
    ActionKind.DELETE_POD.value: frozenset({"namespace", "name"}),
}


def _csv(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in (value or "").split(",") if item.strip())


@dataclass(frozen=True)
class PolicyConfig:
    enabled: bool = False
    kill_switch: bool = False
    allowed_namespaces: frozenset[str] = frozenset()
    denied_namespaces: frozenset[str] = frozenset()
    scale_min: int = 1
    scale_max: int = 5

    @classmethod
    def from_settings(cls, settings: Settings) -> PolicyConfig:
        return cls(
            enabled=settings.actions_enabled,
            kill_switch=settings.actions_kill_switch,
            allowed_namespaces=_csv(settings.actions_allowed_namespaces),
            denied_namespaces=_csv(settings.actions_denied_namespaces) | HARD_DENIED_NAMESPACES,
            scale_min=settings.actions_scale_min,
            scale_max=settings.actions_scale_max,
        )

    def namespace_violation(self, namespace: str) -> str | None:
        if namespace in HARD_DENIED_NAMESPACES or namespace in self.denied_namespaces:
            return f"namespace '{namespace}' is on the deny list"
        if namespace not in self.allowed_namespaces:
            allowed = ", ".join(sorted(self.allowed_namespaces)) or "none"
            return f"namespace '{namespace}' is not on the allow list (allowed: {allowed})"
        return None


@dataclass(frozen=True)
class IncidentScope:
    """The incident the proposal is for. Actions may only touch this pod or its Deployment."""

    namespace: str
    pod: str
    deployment: str | None


@dataclass(frozen=True)
class DeploymentState:
    namespace: str
    name: str
    replicas: int
    ready: int = 0
    available: int = 0
    updated: int = 0
    generation: int = 0
    observed_generation: int = 0
    revision: int | None = None
    previous_revision: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    previous_summary: dict[str, Any] | None = None
    # Field-level pod-template diff previous -> current revision (None: no previous revision / not captured).
    template_diff: list[str] | None = None
    # ConfigMaps/Secrets/PVCs the current template references.
    template_refs: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class PodState:
    namespace: str
    name: str
    controller_kind: str | None  # kind of the controlling ownerReference
    controller_name: str | None
    deployment: str | None  # Deployment that owns the ReplicaSet, if any
    crashlooping: bool
    restart_count: int
    reason: str | None = None


@dataclass(frozen=True)
class TargetState:
    deployment: DeploymentState | None = None
    pod: PodState | None = None


def validate_shape(request: ActionRequest, config: PolicyConfig) -> list[str]:
    """Checks that need no cluster access: allowlist, parameters, namespace, bounds."""
    if request.kind not in ALLOWED_KINDS:
        allowed = ", ".join(sorted(ALLOWED_KINDS))
        return [f"action '{request.kind}' is not on the allowlist ({allowed})"]

    violations: list[str] = []
    params = request.params
    expected = PARAMS[request.kind]
    extra = sorted(set(params) - expected)
    missing = sorted(expected - set(params))
    if extra:
        violations.append(f"unexpected parameters: {', '.join(extra)}")
    if missing:
        violations.append(f"missing parameters: {', '.join(missing)}")
    for key in ("namespace", "name"):
        value = params.get(key)
        if key in params and (not isinstance(value, str) or len(value) > 253 or not _DNS1123.match(value)):
            violations.append(f"'{key}' must be a valid Kubernetes name, got {value!r}")
    namespace = params.get("namespace")
    if isinstance(namespace, str):
        ns_violation = config.namespace_violation(namespace)
        if ns_violation:
            violations.append(ns_violation)

    if request.kind == ActionKind.SCALE_DEPLOYMENT.value and "replicas" in params:
        replicas = params["replicas"]
        if isinstance(replicas, bool) or not isinstance(replicas, int):
            violations.append(f"'replicas' must be an integer, got {replicas!r}")
        elif not config.scale_min <= replicas <= config.scale_max:
            violations.append(
                f"replicas={replicas} is outside the allowed range [{config.scale_min}, {config.scale_max}]"
            )
    return violations


def validate_scope(request: ActionRequest, scope: IncidentScope | None) -> list[str]:
    """The LLM may only act on the incident's own pod or Deployment."""
    if scope is None:
        return []
    namespace = request.params.get("namespace")
    name = request.params.get("name")
    if namespace != scope.namespace:
        return [f"target namespace '{namespace}' is not the incident namespace '{scope.namespace}'"]
    if request.kind == ActionKind.DELETE_POD.value:
        if name != scope.pod:
            return [f"delete_pod may only target the incident pod '{scope.pod}', not '{name}'"]
        return []
    if scope.deployment is None:
        return [f"pod '{scope.pod}' is not managed by a Deployment, so Deployment actions are not allowed"]
    if name != scope.deployment:
        return [f"target Deployment '{name}' is not the incident's Deployment '{scope.deployment}'"]
    return []


def validate_target(request: ActionRequest, target: TargetState) -> list[str]:
    """Checks against live cluster state (read just before the dry run / execution)."""
    kind = request.kind
    params = request.params
    if kind == ActionKind.DELETE_POD.value:
        pod = target.pod
        if pod is None:
            return [f"pod '{params.get('name')}' was not found"]
        violations = []
        if pod.controller_kind is None:
            violations.append("pod has no controller; deleting it would not bring it back")
        elif pod.controller_kind != "ReplicaSet" or pod.deployment is None:
            violations.append(
                f"pod is controlled by {pod.controller_kind} '{pod.controller_name}', "
                "only Deployment-managed pods may be deleted"
            )
        if not pod.crashlooping:
            violations.append("pod is not crashlooping; delete_pod is only for a stuck crashlooping pod")
        return violations

    dep = target.deployment
    if dep is None:
        return [f"Deployment '{params.get('namespace')}/{params.get('name')}' was not found"]
    if kind == ActionKind.SCALE_DEPLOYMENT.value:
        if params.get("replicas") == dep.replicas:
            return [f"Deployment already has {dep.replicas} replicas; scaling is a no-op"]
    if kind == ActionKind.ROLLBACK_DEPLOYMENT.value and dep.previous_revision is None:
        return ["Deployment has no previous revision to roll back to"]
    return []


def validate(
    request: ActionRequest,
    config: PolicyConfig,
    scope: IncidentScope | None = None,
    target: TargetState | None = None,
) -> list[str]:
    violations = validate_shape(request, config)
    if violations:
        return violations
    violations = validate_scope(request, scope)
    if violations:
        return violations
    if target is not None:
        return validate_target(request, target)
    return []
