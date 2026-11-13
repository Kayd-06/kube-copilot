"""Policy is code, not prompt: these cases must be refused no matter what the LLM says."""

import pytest

from app.actions.models import ActionRequest
from app.actions.policy import (
    DeploymentState,
    IncidentScope,
    PodState,
    PolicyConfig,
    TargetState,
    validate,
)
from app.config import Settings

CFG = PolicyConfig(
    enabled=True,
    allowed_namespaces=frozenset({"copilot-demo", "chaos-mesh"}),
    denied_namespaces=frozenset({"chaos-mesh", "kube-system"}),
    scale_min=1,
    scale_max=5,
)
SCOPE = IncidentScope(namespace="copilot-demo", pod="web-abc-12345", deployment="web")
DEP = DeploymentState(namespace="copilot-demo", name="web", replicas=2, revision=3, previous_revision=2)


def req(kind, **params):
    return ActionRequest(kind=kind, params=params, rationale="test")


def test_allowed_restart_passes():
    assert validate(req("restart_deployment", namespace="copilot-demo", name="web"), CFG, SCOPE, TargetState(deployment=DEP)) == []


@pytest.mark.parametrize("kind", ["kubectl_exec", "delete_namespace", "patch_configmap", "cordon_node", "Restart_Deployment", ""])
def test_hallucinated_or_unlisted_actions_are_refused(kind):
    violations = validate(req(kind, namespace="copilot-demo", name="web"), CFG, SCOPE)
    assert violations and "not on the allowlist" in violations[0]


@pytest.mark.parametrize("namespace", ["kube-system", "chaos-mesh"])
def test_denied_namespace_wins_over_allow_list(namespace):
    violations = validate(req("restart_deployment", namespace=namespace, name="web"), CFG)
    assert any("deny list" in v for v in violations)


def test_kube_system_is_refused_even_if_config_allows_it():
    cfg = PolicyConfig(enabled=True, allowed_namespaces=frozenset({"kube-system"}), denied_namespaces=frozenset())
    assert cfg.namespace_violation("kube-system") is not None
    s = Settings(actions_allowed_namespaces="kube-system", actions_denied_namespaces="")
    assert PolicyConfig.from_settings(s).namespace_violation("kube-system") is not None


def test_namespace_not_on_allow_list():
    violations = validate(req("restart_deployment", namespace="default", name="web"), CFG)
    assert any("not on the allow list" in v for v in violations)


def test_default_settings_are_read_only_and_safe():
    s = Settings()
    cfg = PolicyConfig.from_settings(s)
    assert cfg.enabled is False
    for ns in ("kube-system", "chaos-mesh"):
        assert cfg.namespace_violation(ns) is not None


@pytest.mark.parametrize("replicas", [0, 6, 100, -1])
def test_scale_out_of_bounds(replicas):
    violations = validate(req("scale_deployment", namespace="copilot-demo", name="web", replicas=replicas), CFG, SCOPE)
    assert any("outside the allowed range" in v for v in violations)


@pytest.mark.parametrize("replicas", ["3", 2.5, True, None])
def test_scale_requires_integer(replicas):
    violations = validate(req("scale_deployment", namespace="copilot-demo", name="web", replicas=replicas), CFG, SCOPE)
    assert any("must be an integer" in v for v in violations)


def test_scale_to_current_is_noop():
    r = req("scale_deployment", namespace="copilot-demo", name="web", replicas=2)
    assert any("no-op" in v for v in validate(r, CFG, SCOPE, TargetState(deployment=DEP)))
    r3 = req("scale_deployment", namespace="copilot-demo", name="web", replicas=3)
    assert validate(r3, CFG, SCOPE, TargetState(deployment=DEP)) == []


def test_extra_and_missing_params_refused():
    v = validate(req("restart_deployment", namespace="copilot-demo", name="web", force=True), CFG, SCOPE)
    assert any("unexpected parameters: force" in x for x in v)
    v = validate(req("scale_deployment", namespace="copilot-demo", name="web"), CFG, SCOPE)
    assert any("missing parameters: replicas" in x for x in v)


def test_invalid_names_refused():
    v = validate(req("restart_deployment", namespace="copilot-demo", name="web; rm -rf /"), CFG, SCOPE)
    assert any("valid Kubernetes name" in x for x in v)


def test_scope_other_deployment_refused():
    v = validate(req("restart_deployment", namespace="copilot-demo", name="payments"), CFG, SCOPE)
    assert any("not the incident's Deployment" in x for x in v)


def test_scope_other_namespace_refused():
    cfg = PolicyConfig(enabled=True, allowed_namespaces=frozenset({"copilot-demo", "other"}))
    v = validate(req("restart_deployment", namespace="other", name="web"), cfg, SCOPE)
    assert any("not the incident namespace" in x for x in v)


def test_delete_pod_must_be_incident_pod():
    v = validate(req("delete_pod", namespace="copilot-demo", name="web-other-99999"), CFG, SCOPE)
    assert any("only target the incident pod" in x for x in v)


def _pod(**kw):
    base = dict(
        namespace="copilot-demo",
        name="web-abc-12345",
        controller_kind="ReplicaSet",
        controller_name="web-abc",
        deployment="web",
        crashlooping=True,
        restart_count=7,
    )
    base.update(kw)
    return PodState(**base)


def test_delete_crashlooping_controller_pod_ok():
    r = req("delete_pod", namespace="copilot-demo", name="web-abc-12345")
    assert validate(r, CFG, SCOPE, TargetState(pod=_pod())) == []


def test_delete_bare_pod_refused():
    r = req("delete_pod", namespace="copilot-demo", name="web-abc-12345")
    v = validate(r, CFG, SCOPE, TargetState(pod=_pod(controller_kind=None, controller_name=None, deployment=None)))
    assert any("no controller" in x for x in v)


def test_delete_statefulset_pod_refused():
    r = req("delete_pod", namespace="copilot-demo", name="web-abc-12345")
    v = validate(r, CFG, SCOPE, TargetState(pod=_pod(controller_kind="StatefulSet", controller_name="db", deployment=None)))
    assert any("only Deployment-managed" in x for x in v)


def test_delete_healthy_pod_refused():
    r = req("delete_pod", namespace="copilot-demo", name="web-abc-12345")
    v = validate(r, CFG, SCOPE, TargetState(pod=_pod(crashlooping=False)))
    assert any("not crashlooping" in x for x in v)


def test_rollback_without_previous_revision_refused():
    dep = DeploymentState(namespace="copilot-demo", name="web", replicas=1, revision=1, previous_revision=None)
    v = validate(req("rollback_deployment", namespace="copilot-demo", name="web"), CFG, SCOPE, TargetState(deployment=dep))
    assert any("no previous revision" in x for x in v)


def test_missing_target_is_refused():
    v = validate(req("restart_deployment", namespace="copilot-demo", name="web"), CFG, SCOPE, TargetState())
    assert any("was not found" in x for x in v)


def test_deployment_action_for_pod_without_deployment():
    scope = IncidentScope(namespace="copilot-demo", pod="bare", deployment=None)
    v = validate(req("restart_deployment", namespace="copilot-demo", name="bare"), CFG, scope)
    assert any("not managed by a Deployment" in x for x in v)
