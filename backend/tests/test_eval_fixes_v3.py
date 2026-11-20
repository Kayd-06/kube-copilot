"""Context fixes from the PR #33 failures: dependency check, health summary, resource regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kubernetes.client import (
    ApiException,
    V1Container,
    V1EnvVar,
    V1ObjectMeta,
    V1Pod,
    V1PodCondition,
    V1PodSpec,
    V1PodStatus,
    V1Service,
    V1ServiceSpec,
)

from app.actions.policy import DeploymentState
from app.actions.proposer import SYSTEM_PROMPT as PROPOSER_PROMPT
from app.actions.proposer import annotate_diff, build_context
from app.dependencies import check_dependencies, dependency_lines, service_ref
from app.llm_explainer import SYSTEM_PROMPT, build_user_prompt, health_lines
from app.models import (
    ContainerStateSummary,
    ContainerUsage,
    DependencyStatus,
    PodEvent,
    PodLogs,
    PodSummary,
    PodUsage,
)

T0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


def _pod(ready=True, restarts=0, phase="Running", created=T0) -> PodSummary:
    return PodSummary(
        name="web-1", namespace="shop", phase=phase, ready=ready, restart_count=restarts, created_at=created,
        containers=[ContainerStateSummary(name="web", ready=ready, restart_count=restarts, state="running")],
    )


def test_service_ref_only_in_cluster_names():
    assert service_ref("http://deps-api/", "shop") == ("shop", "deps-api")
    assert service_ref("redis:6379", "shop") == ("shop", "redis")
    assert service_ref("postgres://app@db:5432/x", "shop") == ("shop", "db")
    assert service_ref("http://api.payments.svc.cluster.local:8080/v1", "shop") == ("payments", "api")
    for value in ("https://example.com/", "quay.io/acme/x:1", "10.0.0.1:80", "2.3.1", "eu-west-1",
                  "localhost:8080", "smtp.internal", "postgres//app@db:5432/x"):
        assert service_ref(value, "shop") is None, value


class _Core:
    def __init__(self, services, pods):
        self.services, self.pods = services, pods

    def read_namespaced_service(self, name, namespace):
        if name not in self.services:
            raise ApiException(status=404)
        return V1Service(spec=V1ServiceSpec(selector={"app": name}))

    def list_namespaced_pod(self, namespace, label_selector):
        return SimpleNamespace(items=self.pods.get(label_selector.split("=")[1], []))


def _backing(ready_at):
    cond = V1PodCondition(type="Ready", status="True" if ready_at else "False", last_transition_time=ready_at)
    return V1Pod(metadata=V1ObjectMeta(name="p"), status=V1PodStatus(conditions=[cond]))


def test_check_dependencies_reports_missing_down_and_recovered():
    env = [V1EnvVar(name="A", value="http://up/"), V1EnvVar(name="B", value="http://down:80/"),
           V1EnvVar(name="C", value="http://gone/"), V1EnvVar(name="D", value="plain")]
    pod = V1Pod(metadata=V1ObjectMeta(name="web-1", namespace="shop"),
                spec=V1PodSpec(containers=[V1Container(name="web", env=env)]))
    core = _Core({"up", "down"}, {"up": [_backing(T0 + timedelta(seconds=40))], "down": [_backing(None)]})
    deps = {d.service: d for d in check_dependencies(core, pod)}
    assert set(deps) == {"up", "down", "gone"}
    assert deps["up"].ready_pods == 1 and deps["up"].ready_since == T0 + timedelta(seconds=40)
    assert deps["down"].ready_pods == 0 and deps["down"].backing_pods == 1
    assert deps["gone"].exists is False

    text = "\n".join(dependency_lines(list(deps.values()), T0))
    assert "DEPENDENCY RECOVERED AFTER THIS POD STARTED" in text
    assert "DEPENDENCY DOWN NOW" in text
    assert "DEPENDENCY MISSING" in text
    # Ready before the pod started: serving, not "recovered".
    early = DependencyStatus(source="env A", target="http://up/", namespace="shop", service="up", exists=True,
                             backing_pods=1, ready_pods=1, ready_since=T0 - timedelta(minutes=5))
    assert "before this pod was created" in "\n".join(dependency_lines([early], T0))
    # A crashlooping client whose dependency is Ready is not "stale startup state".
    recovered = deps["up"]
    assert "not stale startup state" in "\n".join(dependency_lines([recovered], T0, restarts=3))
    assert dependency_lines([], T0) == []


def test_health_summary_ignores_logs_and_flags_real_problems():
    healthy = "\n".join(health_lines(_pod(), []))
    assert "HEALTHY by every Kubernetes signal" in healthy

    warn = PodEvent(type="Warning", reason="Unhealthy", message="Readiness probe failed")
    stuck = "\n".join(health_lines(_pod(ready=False), [warn]))
    assert "NOT healthy" in stuck and "not all containers are Ready" in stuck

    throttled = PodUsage(containers=[ContainerUsage(name="web", cpu_throttled_ratio=0.9)])
    assert "CPU-throttled" in "\n".join(health_lines(_pod(), [], usage=throttled))

    down = DependencyStatus(source="env A", target="http://x/", namespace="shop", service="x", exists=True,
                            backing_pods=0, ready_pods=0)
    assert "dependency it points at is down" in "\n".join(health_lines(_pod(), [], dependencies=[down]))
    assert "completed successfully" in "\n".join(health_lines(_pod(ready=False, phase="Succeeded"), []))


def test_diagnose_prompt_has_health_summary_dependencies_and_rules():
    dep = DependencyStatus(source="env UPSTREAM", target="http://deps-api/", namespace="shop", service="deps-api",
                           exists=True, backing_pods=1, ready_pods=1, ready_since=T0 + timedelta(seconds=30))
    prompt = build_user_prompt(_pod(ready=False), [], PodLogs(current="x", container="web"), dependencies=[dep])
    assert prompt.index("Health summary") < prompt.index("Dependencies referenced")
    assert "RECOVERED AFTER THIS POD STARTED" in prompt
    assert "Dependencies referenced" not in build_user_prompt(_pod(), [], PodLogs(current="x", container="web"))
    for phrase in ("holding stale state from startup", "probe misconfiguration", "Start from the health summary"):
        assert phrase in SYSTEM_PROMPT


def test_resource_regression_is_spelled_out_for_the_proposer():
    line = '~ spec.containers[enc].resources.limits.cpu: "250m" -> "15m"'
    assert annotate_diff(line).endswith("(LOWERED cpu limit to 6% of the previous value)")
    assert "raised memory request" in annotate_diff('~ spec.containers[x].resources.requests.memory: "64Mi" -> "1Gi"')
    assert annotate_diff('~ spec.containers[x].env[A].value: "1" -> "2"') == '~ spec.containers[x].env[A].value: "1" -> "2"'

    dep = DeploymentState(namespace="shop", name="enc", replicas=1, ready=1, available=1, revision=2,
                          summary={"template": {}}, previous_revision=1, previous_summary={},
                          template_diff=[line], template_refs={})
    usage = PodUsage(containers=[ContainerUsage(name="enc", cpu_cores=0.007, cpu_limit_cores=0.015,
                                                cpu_throttled_ratio=1.0)])
    down = DependencyStatus(source="env A", target="http://x/", namespace="shop", service="x", exists=False,
                            note="no Service named x in namespace shop; the name will not resolve")
    ctx = build_context(_pod(), [], PodLogs(current="ok"), dep, usage=usage, dependencies=[down])
    assert "LOWERED cpu limit" in ctx
    assert "## Resource usage" in ctx and "100% of CFS periods HIGH" in ctx
    assert "## Dependencies" in ctx and "DEPENDENCY MISSING" in ctx
    assert "Being Ready is not proof" in PROPOSER_PROMPT
    assert "## Resource usage" not in build_context(_pod(), [], PodLogs(current="ok"), dep)


def test_service_target_port_mismatch_is_reported():
    from kubernetes.client import V1ContainerPort, V1ServicePort

    from app.dependencies import _port_mismatch

    svc = V1Service(spec=V1ServiceSpec(selector={"app": "inv"}, ports=[V1ServicePort(port=80, target_port=8080)]))
    backing = V1Pod(spec=V1PodSpec(containers=[V1Container(name="inv", ports=[V1ContainerPort(container_port=80)])]))
    assert _port_mismatch(svc, [backing]) == "Service port 80 -> targetPort 8080, but its pods declare containerPort(s) [80]"
    ok = V1Service(spec=V1ServiceSpec(selector={"app": "inv"}, ports=[V1ServicePort(port=80, target_port=80)]))
    assert _port_mismatch(ok, [backing]) is None
    no_ports = V1Pod(spec=V1PodSpec(containers=[V1Container(name="inv")]))
    assert _port_mismatch(svc, [no_ports]) is None
