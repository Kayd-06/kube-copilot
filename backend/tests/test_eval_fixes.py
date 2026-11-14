"""Context fixes from the LLM eval: log compaction, template diffs, resource usage, prompt rules."""

from __future__ import annotations

from types import SimpleNamespace

from kubernetes.client import (
    V1Container,
    V1ContainerStatus,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1PodStatus,
    V1PodTemplateSpec,
    V1ResourceRequirements,
)
from openai import OpenAIError

from app.actions.cluster import template_diff, template_refs
from app.actions.policy import DeploymentState, PolicyConfig
from app.actions.proposer import ActionProposer, build_context
from app.config import Settings
from app.k8s_client import to_pod_summary
from app.llm_explainer import SYSTEM_PROMPT, build_user_prompt
from app.logcompact import compact_logs
from app.models import ContainerUsage, PodUsage
from app.rag.retriever import build_retrieval_query


def _template(env=None, probe=None, cpu_limit=None, node_selector=None, env_from=None, labels=None):
    container = {"name": "web", "image": "nginx:1.27", "resources": {"limits": {"memory": "64Mi"}}}
    if env:
        container["env"] = [{"name": k, "value": v} for k, v in env.items()]
    if env_from:
        container["envFrom"] = [{"configMapRef": {"name": env_from}}]
    if probe:
        container["livenessProbe"] = probe
    if cpu_limit:
        container["resources"]["limits"]["cpu"] = cpu_limit
    container["terminationMessagePath"] = "/dev/termination-log"
    spec = {"containers": [container], "dnsPolicy": "ClusterFirst", "restartPolicy": "Always"}
    if node_selector:
        spec["nodeSelector"] = node_selector
    return {"metadata": {"labels": {"app": "web", "pod-template-hash": "abc", **(labels or {})}}, "spec": spec}


def test_template_diff_shows_fields_image_and_command_miss():
    before = _template(env={"PORT": "8080"})
    after = _template(env={"PORT": "eighty"}, probe={"httpGet": {"path": "/healthz", "port": 80}},
                      cpu_limit="15m", node_selector={"disktype": "ssd"})
    after["metadata"]["labels"]["pod-template-hash"] = "def"
    diff = template_diff(before, after)
    assert '~ spec.containers[web].env[PORT].value: "8080" -> "eighty"' in diff
    assert '+ spec.containers[web].livenessProbe.httpGet.path: "/healthz"' in diff
    assert '+ spec.containers[web].resources.limits.cpu: "15m"' in diff
    assert '+ spec.nodeSelector.disktype: "ssd"' in diff
    assert not any("pod-template-hash" in line or "dnsPolicy" in line for line in diff)
    assert template_diff(before, before) == []


def test_template_diff_accepts_k8s_objects_and_lists_refs():
    tmpl = V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels={"app": "web"}),
        spec=V1PodSpec(containers=[V1Container(name="web", image="nginx:1.27")]),
    )
    newer = V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels={"app": "web"}),
        spec=V1PodSpec(containers=[V1Container(name="web", image="nginx:1.28")]),
    )
    assert template_diff(tmpl, newer) == ['~ spec.containers[web].image: "nginx:1.27" -> "nginx:1.28"']
    assert template_refs(_template(env_from="cart-flags")) == {"configmaps": ["cart-flags"]}


def test_proposer_context_includes_template_changes():
    pod = to_pod_summary(_pod())
    dep = DeploymentState(namespace="shop", name="web", replicas=1, revision=2, previous_revision=1,
                          summary={"template": {}}, previous_summary={},
                          template_diff=['~ spec.containers[web].env[PORT].value: "8080" -> "eighty"'])
    context = build_context(pod, [], SimpleNamespace(current="x", previous=None), dep)
    assert "Changes in the latest rollout" in context and '"eighty"' in context
    dep_no_diff = DeploymentState(namespace="shop", name="web", replicas=1, revision=1)
    assert "Changes in the latest rollout" not in build_context(pod, [], SimpleNamespace(current="", previous=None),
                                                                 dep_no_diff)


def _pod():
    return V1Pod(
        metadata=V1ObjectMeta(name="web-1", namespace="shop"),
        spec=V1PodSpec(containers=[V1Container(
            name="web", image="nginx",
            resources=V1ResourceRequirements(requests={"cpu": "10m"}, limits={"cpu": "20m", "memory": "64Mi"}),
        )]),
        status=V1PodStatus(phase="Running", container_statuses=[V1ContainerStatus(
            name="web", image="nginx", image_id="", ready=True, restart_count=0)]),
    )


def test_pod_summary_carries_resources_and_prompt_shows_throttling(sample_events, sample_logs):
    pod = to_pod_summary(_pod())
    assert pod.containers[0].resources == {"requests": {"cpu": "10m"}, "limits": {"cpu": "20m", "memory": "64Mi"}}
    usage = PodUsage(containers=[ContainerUsage(name="web", cpu_cores=0.0199, cpu_limit_cores=0.02,
                                                cpu_throttled_ratio=0.93, memory_mib=3.0, memory_limit_mib=64)])
    prompt = build_user_prompt(pod, sample_events, sample_logs, usage=usage)
    assert '"limits"' in prompt and "CPU-throttled in 93% of CFS periods HIGH" in prompt
    assert "100% of limit" in prompt
    assert "CPU throttling" in build_retrieval_query(pod, [], sample_logs, usage)
    assert "CPU throttling" not in build_retrieval_query(pod, [], sample_logs, None)


def test_system_prompt_weights_evidence():
    assert "weak, uncalibrated evidence" in SYSTEM_PROMPT
    assert "pod disruption" in SYSTEM_PROMPT
    assert "CPU-throttled" in SYSTEM_PROMPT


def test_compact_logs_keeps_errors_and_tail_within_budget():
    lines = [f"2026-11-13T17:48:{i % 60:02d}Z 10.0.0.{i % 9} GET /healthz 404 {i}" for i in range(300)]
    lines.insert(150, "2026-11-13T17:49:00Z FATAL cannot open /etc/app.conf: no such file")
    lines.append("2026-11-13T17:50:00Z INFO shutting down")
    text = "\n".join(lines)
    out = compact_logs(text, 1500)
    assert len(out) < 2000
    assert "FATAL cannot open /etc/app.conf" in out
    assert out.rstrip().endswith("INFO shutting down")
    assert "consecutive similar lines" in out or "omitted" in out
    assert compact_logs("short log", 1500) == "short log"
    assert compact_logs(None, 100) == ""


def test_compact_logs_keeps_traceback_context():
    noise = "\n".join(f"INFO tick {i} " + "x" * 60 for i in range(200))
    tb = "Traceback (most recent call last):\n  File \"/app/worker.py\", line 42\n    handler = H[t]\nKeyError: 'v2'"
    out = compact_logs(noise + "\n" + tb + "\n" + noise, 2500)
    assert "Traceback" in out and "KeyError: 'v2'" in out


class _Completions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["tool_choice"] == "required":
            raise OpenAIError("Error code: 400 - Tool choice is required, but model did not call a tool")
        message = SimpleNamespace(tool_calls=None, content="The pod is healthy; nothing to do.")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_proposer_retries_when_model_skips_required_tool():
    completions = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    proposer = ActionProposer(settings=Settings(_env_file=None, llm_api_key="k"), client=client)
    result = proposer.propose("ctx", PolicyConfig())
    assert [c["tool_choice"] for c in completions.calls] == ["required", "auto"]
    assert result.requests == [] and result.retried_without_required_tool
    assert "healthy" in result.note
