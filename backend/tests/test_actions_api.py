"""Safe-actions API with a mocked cluster and a mocked LLM proposer."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.actions.cluster import ActionExecutionError
from app.actions.models import ActionStatus, Preview
from app.actions.policy import DeploymentState, PodState, TargetState
from app.actions.proposer import ProposalResult, parse_tool_calls
from app.actions.service import ActionService
from app.actions.store import ActionStore
from app.config import Settings
from app.deps import get_action_service
from app.main import app
from tests.conftest import FakeK8s

NS, POD, DEP = "copilot-demo", "web-6d5f7-abcde", "web"


class FakeCluster:
    def __init__(self):
        self.replicas = 2
        self.revision = 3
        self.previous_revision = 2
        self.crashlooping = True
        self.applied: list[tuple[str, dict, bool]] = []
        self.health_script: list[tuple[int, list[str]]] = []  # (ready, unhealthy) per poll
        self.fail_apply = False

    def deployment_state(self, namespace, name):
        if name != DEP:
            return None
        return DeploymentState(
            namespace=namespace, name=name, replicas=self.replicas, ready=self.replicas, available=self.replicas,
            updated=self.replicas, revision=self.revision, previous_revision=self.previous_revision,
            summary={"replicas": self.replicas, "template": {"containers": [{"name": "app", "image": "web:v2"}]}},
            previous_summary={"containers": [{"name": "app", "image": "web:v1"}]},
        )

    def pod_state(self, namespace, name):
        if name != POD:
            return None
        return PodState(namespace=namespace, name=name, controller_kind="ReplicaSet", controller_name="web-6d5f7",
                        deployment=DEP, crashlooping=self.crashlooping, restart_count=6)

    def target_state(self, kind, params):
        if kind == "delete_pod":
            return TargetState(pod=self.pod_state(params["namespace"], params["name"]))
        return TargetState(deployment=self.deployment_state(params["namespace"], params["name"]))

    def preview(self, kind, params):
        self.applied.append((kind, params, True))
        return Preview(dry_run_ok=True, before={"replicas": self.replicas}, after={"replicas": params.get("replicas", self.replicas)}, diff="-a\n+b")

    def apply(self, kind, params, dry_run):
        self.applied.append((kind, params, dry_run))
        if self.fail_apply:
            raise ActionExecutionError("forbidden")
        before = {"replicas": self.replicas, "revision": self.revision}
        if kind == "scale_deployment":
            self.replicas = params["replicas"]
        return before, {"replicas": self.replicas}

    def workload_health(self, namespace, deployment):
        ready, unhealthy = self.health_script.pop(0) if self.health_script else (self.replicas, [])
        state = DeploymentState(namespace=namespace, name=deployment, replicas=self.replicas, ready=ready,
                                available=ready, updated=self.replicas)
        return state, unhealthy, {}


class FakeProposer:
    def __init__(self, calls):
        self.calls = calls
        self.contexts = []

    def propose(self, context, config):
        self.contexts.append(context)
        return parse_tool_calls(self.calls)


def call(tool, **args):
    import json

    return {"name": tool, "arguments": json.dumps(args)}


@pytest.fixture
def make(monkeypatch, sample_pod, sample_events, sample_logs):
    monkeypatch.setattr("app.k8s_client.to_pod_summary", lambda pod: pod)
    pod = sample_pod.model_copy(update={"name": POD, "namespace": NS})

    def _make(calls, **overrides):
        overrides.setdefault("actions_enabled", True)
        settings = Settings(
            actions_allowed_namespaces=NS, actions_verify_window_seconds=30,
            actions_verify_interval_seconds=5, actions_verify_stable_seconds=10, **overrides,
        )
        clock = {"t": 0.0}
        cluster = FakeCluster()
        service = ActionService(
            settings=settings, cluster=cluster, proposer=FakeProposer(calls), store=ActionStore(":memory:"),
            k8s=FakeK8s(evidence={(NS, POD): (pod, sample_events, sample_logs)}),
            sleep=lambda s: clock.__setitem__("t", clock["t"] + s), monotonic=lambda: clock["t"],
            verify_in_background=False,
        )
        app.dependency_overrides[get_action_service] = lambda: service
        return TestClient(app), service, cluster

    yield _make
    app.dependency_overrides.clear()


APPROVE = {"actor": "kunal"}


def propose(client):
    r = client.post(f"/pods/{NS}/{POD}/actions/propose")
    assert r.status_code == 200, r.text
    return r.json()


def test_disabled_by_default(make):
    client, *_ = make([], actions_enabled=False)
    r = client.post(f"/pods/{NS}/{POD}/actions/propose")
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]
    assert client.get("/actions/config").json()["enabled"] is False


def test_propose_approve_execute_verify(make):
    client, service, cluster = make([call("rollback_deployment", namespace=NS, name=DEP, reason="v2 image is broken")])
    body = propose(client)
    [action] = body["actions"]
    assert action["status"] == "proposed"
    assert action["preview"]["dry_run_ok"] is True
    # Nothing was written for real before approval: only dry runs.
    assert all(dry for _, _, dry in cluster.applied)
    cluster.health_script = [(0, ["web-x (ImagePullBackOff)"]), (1, []), (2, []), (2, []), (2, [])]
    r = client.post(f"/actions/{action['id']}/approve", json=APPROVE)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "resolved"
    assert done["verification"]["time_to_recovery_seconds"] == 10.0
    assert done["revertible"] is True
    assert ("rollback_deployment", {"namespace": NS, "name": DEP}, False) in cluster.applied
    events = [e["event"] for e in client.get(f"/actions/{action['id']}").json()["timeline"]]
    assert events == ["proposed", "approved", "executed", "verification_started", "verified_resolved"]


def test_not_resolved_when_never_healthy(make):
    client, _, cluster = make([call("restart_deployment", namespace=NS, name=DEP, reason="flaky")])
    [action] = propose(client)["actions"]
    cluster.health_script = [(0, ["web-x (CrashLoopBackOff)"])] * 20
    done = client.post(f"/actions/{action['id']}/approve", json=APPROVE).json()
    assert done["status"] == "not_resolved"
    assert "CrashLoopBackOff" in done["verification"]["reason"]
    assert done["revertible"] is False


def test_hallucinated_action_is_blocked_and_audited(make):
    client, _, cluster = make([
        call("kubectl_exec", namespace=NS, name=POD, command="rm -rf /", reason="trust me"),
        call("scale_deployment", namespace=NS, name=DEP, replicas=50, reason="more is better"),
        call("restart_deployment", namespace="kube-system", name="coredns", reason="dns"),
    ])
    actions = propose(client)["actions"]
    assert [a["status"] for a in actions] == ["blocked", "blocked", "blocked"]
    assert "not on the allowlist" in actions[0]["violations"][0]
    assert "outside the allowed range" in actions[1]["violations"][0]
    assert "deny list" in actions[2]["violations"][0]
    assert cluster.applied == []  # not even a dry run
    for a in actions:
        r = client.post(f"/actions/{a['id']}/approve", json=APPROVE)
        assert r.status_code == 409
    audit = [e["event"] for e in client.get("/audit").json()["events"]]
    assert audit.count("policy_blocked") == 3


def test_reject_records_human_and_cannot_approve_after(make):
    client, _, cluster = make([call("restart_deployment", namespace=NS, name=DEP, reason="x")])
    [action] = propose(client)["actions"]
    r = client.post(f"/actions/{action['id']}/reject", json={"actor": "kunal", "comment": "not now"})
    assert r.json()["status"] == "rejected" and r.json()["decided_by"] == "kunal"
    assert client.post(f"/actions/{action['id']}/approve", json=APPROVE).status_code == 409
    assert not any(not dry for _, _, dry in cluster.applied)


def test_kill_switch_blocks_execution(make):
    client, _, cluster = make([call("restart_deployment", namespace=NS, name=DEP, reason="x")], actions_kill_switch=True)
    body = propose(client)
    assert body["kill_switch"] is True
    r = client.post(f"/actions/{body['actions'][0]['id']}/approve", json=APPROVE)
    assert r.status_code == 423
    assert not any(not dry for _, _, dry in cluster.applied)
    assert "kill_switch_blocked" in [e["event"] for e in client.get("/audit").json()["events"]]


def test_cooldown_rate_limits_same_workload(make):
    client, _, _ = make([
        call("restart_deployment", namespace=NS, name=DEP, reason="a"),
        call("scale_deployment", namespace=NS, name=DEP, replicas=3, reason="b"),
    ])
    first, second = propose(client)["actions"]
    assert client.post(f"/actions/{first['id']}/approve", json=APPROVE).status_code == 200
    r = client.post(f"/actions/{second['id']}/approve", json=APPROVE)
    assert r.status_code == 429
    assert "cooldown" in r.json()["detail"]


def test_one_in_flight_per_workload(make):
    client, service, _ = make([
        call("restart_deployment", namespace=NS, name=DEP, reason="a"),
        call("scale_deployment", namespace=NS, name=DEP, replicas=3, reason="b"),
    ], actions_cooldown_seconds=0)
    first, second = propose(client)["actions"]
    rec = service.store.get(first["id"])
    rec.status = ActionStatus.VERIFYING
    service.store.save(rec)
    r = client.post(f"/actions/{second['id']}/approve", json=APPROVE)
    assert r.status_code == 409 and "still running" in r.json()["detail"]


def test_policy_rechecked_at_approval(make):
    client, _, cluster = make([call("delete_pod", namespace=NS, name=POD, reason="stuck")])
    [action] = propose(client)["actions"]
    assert action["status"] == "proposed"
    cluster.crashlooping = False  # pod recovered on its own before approval
    r = client.post(f"/actions/{action['id']}/approve", json=APPROVE)
    assert r.status_code == 409 and "not crashlooping" in r.json()["detail"]
    assert client.get(f"/actions/{action['id']}").json()["status"] == "blocked"


def test_execution_failure_is_recorded(make):
    client, _, cluster = make([call("restart_deployment", namespace=NS, name=DEP, reason="x")])
    [action] = propose(client)["actions"]
    cluster.fail_apply = True
    done = client.post(f"/actions/{action['id']}/approve", json=APPROVE).json()
    assert done["status"] == "failed" and "forbidden" in done["error"]


def test_scale_revert_is_a_new_proposal_needing_approval(make):
    client, _, cluster = make([call("scale_deployment", namespace=NS, name=DEP, replicas=4, reason="load")], actions_cooldown_seconds=0)
    [action] = propose(client)["actions"]
    done = client.post(f"/actions/{action['id']}/approve", json=APPROVE).json()
    assert done["status"] == "resolved" and cluster.replicas == 4
    revert = client.post(f"/actions/{action['id']}/revert", json=APPROVE).json()
    assert revert["status"] == "proposed" and revert["params"]["replicas"] == 2
    assert revert["reverts_action_id"] == action["id"] and cluster.replicas == 4
    assert client.post(f"/actions/{revert['id']}/approve", json=APPROVE).json()["status"] == "resolved"
    assert cluster.replicas == 2


def test_restart_has_no_revert(make):
    client, _, _ = make([call("restart_deployment", namespace=NS, name=DEP, reason="x")])
    [action] = propose(client)["actions"]
    client.post(f"/actions/{action['id']}/approve", json=APPROVE)
    assert client.post(f"/actions/{action['id']}/revert", json=APPROVE).status_code == 409


def test_denied_namespace_does_not_call_llm(make):
    client, service, _ = make([call("restart_deployment", namespace="kube-system", name="coredns", reason="x")])
    r = client.post("/pods/kube-system/coredns-abc/actions/propose")
    assert r.status_code == 200 and r.json()["actions"] == []
    assert "deny list" in r.json()["note"]
    assert service.proposer.contexts == []


def test_no_action_note(make):
    client, _, _ = make([call("no_action", reason="needs a code fix")])
    body = propose(client)
    assert body["actions"] == [] and "code fix" in body["note"]


def test_expired_proposal(make):
    client, _, _ = make([call("restart_deployment", namespace=NS, name=DEP, reason="x")], actions_proposal_ttl_seconds=-1)
    [action] = propose(client)["actions"]
    r = client.post(f"/actions/{action['id']}/approve", json=APPROVE)
    assert r.status_code == 409 and "expired" in r.json()["detail"]


def test_unparseable_tool_arguments_are_blocked():
    result = parse_tool_calls([{"name": "restart_deployment", "arguments": "{not json"}])
    assert result.requests[0].params.get("_unparsed")


def test_audit_persists_across_store_instances(tmp_path):
    path = tmp_path / "a.sqlite"
    ActionStore(path).audit("proposed", "llm", "abc", kind="restart_deployment")
    events = ActionStore(path).audit_events()
    assert events[0].event == "proposed" and events[0].detail["kind"] == "restart_deployment"


def test_proposal_result_type():
    assert isinstance(parse_tool_calls([]), ProposalResult)
