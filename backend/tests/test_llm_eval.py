"""Offline checks for the LLM eval harness: fixtures, labels, prompts and scorers. No API key needed."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.actions.proposer import build_context
from app.llm_explainer import build_user_prompt
from ml.llm_eval.cases import CASES, CASES_BY_ID
from ml.llm_eval.fixtures import FIXTURE_DIR, FIXTURE_DIR_V1, load_all
from ml.llm_eval.run import Pacer, baselines, diagnose_inputs, runs_variant, summarize
from ml.llm_eval.scoring import kubectl_refs, schema_check, score_hallucination, score_remediation, score_retrieval
from ml.llm_eval.taxonomy import ACTIONS, CATEGORIES, matches, predict_category

RUNBOOKS = {p.name for p in (FIXTURE_DIR.parents[2].parent / "knowledge" / "runbooks").glob("*.md")}
FIXTURES = load_all()
FIXTURES_V1 = load_all(FIXTURE_DIR_V1)


def test_fixture_set_is_complete_and_sized():
    for fixtures in (FIXTURES, FIXTURES_V1):
        assert 25 <= len(fixtures) <= 40
        assert {f.id for f in fixtures} == set(CASES_BY_ID), "every case has exactly one fixture"


def test_v2_recaptures_keep_labels_and_add_context():
    v1 = {f.id: f for f in FIXTURES_V1}
    for fx in FIXTURES:
        assert fx.labels == v1[fx.id].labels, "re-captures must not change labels"
        if fx.raw["schema_version"] == 2 and fx.deployment and fx.deployment.previous_revision:
            assert fx.deployment.template_diff is not None


@pytest.mark.parametrize("fx", FIXTURES + FIXTURES_V1, ids=lambda f: f"{f.raw['schema_version']}-{f.id}")
def test_fixture_schema_and_labels(fx):
    case = CASES_BY_ID[fx.id]
    assert fx.raw["schema_version"] in (1, 2)
    assert fx.labels == case.labels(), "fixture labels must match the hand labels in cases.py"
    assert fx.labels["category"] in CATEGORIES
    assert fx.labels["expected_actions"] and set(fx.labels["expected_actions"]) <= set(ACTIONS)
    assert set(fx.labels["expected_runbooks"]) <= RUNBOOKS
    assert fx.labels["difficulty"] in {"easy", "medium", "hard"}
    assert fx.pod.namespace == fx.namespace and fx.pod.name == fx.raw["pod_name"]
    # Neutral names: nothing in the context may leak the label.
    blob = json.dumps(fx.raw["diagnose_input"]) + json.dumps(fx.raw["propose_input"])
    assert fx.id not in blob
    if "_" in fx.labels["category"]:
        assert fx.labels["category"] not in blob
    # Prompts build exactly as the API builds them.
    assert fx.pod.name in build_user_prompt(*diagnose_inputs(fx, "full"))
    assert fx.pod.name in build_context(fx.pod, fx.events, fx.logs, fx.deployment, fx.signals)
    # Rollout cases really have a previous revision to roll back to.
    if "rollback_deployment" in fx.labels["expected_actions"] and fx.labels["category"] != "dependency_connectivity":
        assert fx.deployment is not None and fx.deployment.previous_revision is not None


def test_dataset_has_hard_and_healthy_cases():
    cats = [f.labels["category"] for f in FIXTURES]
    assert cats.count("healthy") >= 2
    assert sum(f.labels["difficulty"] == "hard" for f in FIXTURES) >= 4
    assert len(set(cats)) >= 10


def test_case_ids_unique():
    assert len({c.id for c in CASES}) == len(CASES)


@pytest.mark.parametrize(
    "category,text",
    [
        ("image_pull", "The image tag nginx:1.27-alpine-typo does not exist in the registry"),
        ("oom_killed", "The container is OOMKilled because it exceeds its 48Mi memory limit"),
        ("missing_config", 'ConfigMap "cart-feature-flags" referenced by envFrom does not exist'),
        ("probe_failure", "The liveness probe on /healthz returns 404"),
        ("unschedulable", "The pod requests 64 CPUs, more than any node has, so it cannot be scheduled"),
        ("pvc_pending", "The PVC uses a StorageClass fast-ssd that does not exist"),
        ("dependency_connectivity", "The upstream inventory service refuses connections on port 80"),
        ("pod_disruption", "The previous replica was killed and replaced by the ReplicaSet"),
        ("healthy", "No issue: the pod is healthy and running normally"),
        ("bad_config_value", "Invalid value for the PORT environment variable ('eighty')"),
        ("cpu_throttling", "The CPU limit of 20m is too low and the container is throttled"),
        ("app_crash", "The container command /app/server does not exist, so the process exits immediately"),
    ],
)
def test_category_matchers(category, text):
    assert matches(category, text)


def test_matchers_do_not_cross_obvious_categories():
    assert not matches("oom_killed", "The image tag does not exist")
    assert not matches("image_pull", "The process exits with code 1 due to a KeyError")
    assert predict_category("Liveness probe fails, so the kubelet restarts the container") == "probe_failure"


def test_schema_check():
    good = json.dumps({"summary": "s", "likely_root_cause": "r", "suggested_fix": "f",
                       "kubectl_commands": ["kubectl get pods"], "confidence": 0.8})
    assert schema_check(good)[0]
    assert schema_check("```json\n" + good + "\n```")[0]
    assert not schema_check("not json")[0]
    assert not schema_check(json.dumps({"summary": "s"}))[0]
    assert not schema_check(good.replace("0.8", "1.5"))[0]


def test_kubectl_refs_and_hallucination():
    refs = kubectl_refs("kubectl describe pod web-abc -n shop")
    assert refs == {"namespaces": ["shop"], "names": ["web-abc"]}
    assert kubectl_refs("kubectl rollout undo deployment/web -n shop")["names"] == ["web"]
    assert kubectl_refs("kubectl logs web-abc --previous -n shop")["names"] == ["web-abc"]
    assert kubectl_refs("kubectl get events -n shop --field-selector involvedObject.name=web-abc")["names"] == ["web-abc"]
    out = score_hallucination(
        ["kubectl describe pod web-abc -n shop", "kubectl get svc payments-db -n shop", "kubectl get endpoints <svc> -n shop"],
        "Pod: shop/web-abc",
    )
    assert out["unknown_refs"] == ["name:payments-db"] and out["placeholders"] == ["<svc>"]


def test_remediation_and_retrieval_scoring():
    r = score_remediation(["rollback_deployment"], ["rollback_deployment"], [{"namespace": "a", "name": "web"}], "a", "web", "web-1")
    assert r["correct"] and not r["target_errors"]
    r = score_remediation(["no_action"], [], [], "a", "web", "web-1")
    assert r["correct"] and r["primary"] == "no_action"
    r = score_remediation(["no_action"], ["delete_pod"], [{"namespace": "a", "name": "other"}], "a", "web", "web-1")
    assert not r["correct"] and r["target_errors"]
    assert score_retrieval(["oomkilled.md"], ["crashloopbackoff.md", "oomkilled.md"])["rank"] == 2
    assert score_retrieval([], ["x.md"]) is None


def test_variants_and_summary_offline():
    fx = FIXTURES[0]
    assert runs_variant(fx, "diagnose", "full")
    _, _, _, retrieved, _, _ = diagnose_inputs(fx, "no_rag")
    assert retrieved is None
    records = [{
        "case": fx.id, "task": "diagnose", "variant": "full", "latency_s": 1.0, "prompt_tokens": 10,
        "completion_tokens": 5, "schema_valid": True, "diagnosis": {"confidence": 0.5},
        "category": {"correct": True}, "hallucination": {"hallucinated": False, "placeholders": []},
    }]
    s = summarize(records, {fx.id: fx}, "openai/gpt-oss-120b")
    assert s["diagnose/full"]["category_accuracy"] == 100.0
    b = baselines(FIXTURES)
    assert b["cases"] == len(FIXTURES) and b["retrieval"]["cases_with_expected_runbook"] > 0


def test_pacer_retries_rate_limits(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = SimpleNamespace(n=0)

    def flaky():
        calls.n += 1
        if calls.n == 1:
            raise RuntimeError("Error code: 429 - rate_limit_exceeded. Please try again in 1.5s.")
        return "ok"

    out, _ = Pacer(tpm=0).call(flaky, 100)
    assert out == "ok" and calls.n == 2


def test_pacer_waits_for_daily_quota_when_allowed(monkeypatch):
    slept = []
    monkeypatch.setattr("time.sleep", slept.append)
    calls = SimpleNamespace(n=0)

    def tpd():
        calls.n += 1
        if calls.n == 1:
            raise RuntimeError("429 Rate limit reached on tokens per day (TPD): Limit 200000. Please try again in 2m3.5s.")
        return "ok"

    out, _ = Pacer(tpm=0, wait_daily_s=600).call(tpd, 100)
    assert out == "ok" and slept and slept[0] >= 123
    calls.n = 0
    with pytest.raises(Exception, match="TPD"):
        Pacer(tpm=0, wait_daily_s=0).call(tpd, 100)
