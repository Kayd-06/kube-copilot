"""Orchestrates propose -> policy -> dry run -> human approval -> execute -> verify, with an audit trail."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.actions.cluster import ActionExecutionError, ActionsCluster
from app.actions.models import (
    ALLOWED_KINDS,
    EXECUTED,
    IN_FLIGHT,
    ActionDetail,
    ActionKind,
    ActionRecord,
    ActionRequest,
    ActionsConfigOut,
    ActionStatus,
    ProposeResponse,
    Verification,
    VerificationSample,
)
from app.actions.policy import IncidentScope, PolicyConfig, validate
from app.actions.proposer import ActionProposer, build_context
from app.actions.store import ActionStore
from app.config import Settings

logger = logging.getLogger(__name__)

# Lifecycle signals a rollout, restart, scale or pod delete produces by design.
EXPECTED_AFTER_ACTION = frozenset(
    {
        "pod_replaced",
        "pod_vanished",
        "replicas_unavailable",
        "event:Killing",
        "event:SuccessfulCreate",
        "event:SuccessfulDelete",
        "event:ScalingReplicaSet",
    }
)


class ActionError(Exception):
    status_code = 400


class ActionsDisabledError(ActionError):
    status_code = 403


class ActionNotFoundError(ActionError):
    status_code = 404


class ActionConflictError(ActionError):
    status_code = 409


class KillSwitchError(ActionError):
    status_code = 423


class RateLimitedError(ActionError):
    status_code = 429


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ActionService:
    def __init__(
        self,
        settings: Settings,
        cluster: ActionsCluster,
        proposer: ActionProposer,
        store: ActionStore,
        k8s: Any = None,
        lifecycle: Any = None,
        scorer: Any = None,
        prometheus: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        verify_in_background: bool = True,
    ) -> None:
        self.settings = settings
        self.cluster = cluster
        self.proposer = proposer
        self.store = store
        self.k8s = k8s
        self.lifecycle = lifecycle
        self.scorer = scorer
        self.prometheus = prometheus
        self._sleep = sleep
        self._monotonic = monotonic
        self._verify_in_background = verify_in_background
        self._lock = threading.Lock()

    @property
    def policy(self) -> PolicyConfig:
        return PolicyConfig.from_settings(self.settings)

    def config_view(self) -> ActionsConfigOut:
        p = self.policy
        s = self.settings
        return ActionsConfigOut(
            enabled=p.enabled,
            kill_switch=p.kill_switch,
            allowed_namespaces=sorted(p.allowed_namespaces - p.denied_namespaces),
            denied_namespaces=sorted(p.denied_namespaces),
            allowed_actions=sorted(ALLOWED_KINDS),
            scale_min=p.scale_min,
            scale_max=p.scale_max,
            cooldown_seconds=s.actions_cooldown_seconds,
            max_per_hour=s.actions_max_per_hour,
            proposal_ttl_seconds=s.actions_proposal_ttl_seconds,
            verify_window_seconds=s.actions_verify_window_seconds,
        )

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise ActionsDisabledError(
                "Safe actions are disabled (read-only mode). Set ACTIONS_ENABLED=true "
                "(Helm: actions.enabled=true) to allow proposals."
            )

    # ------------------------------------------------------------------ propose
    def propose(self, namespace: str, pod_name: str, actor: str = "system") -> ProposeResponse:
        self._require_enabled()
        policy = self.policy
        self.store.audit("proposal_requested", actor, None, namespace=namespace, pod=pod_name)
        ns_violation = policy.namespace_violation(namespace)
        if ns_violation:
            self.store.audit("proposal_refused", "policy", None, namespace=namespace, pod=pod_name, reason=ns_violation)
            return ProposeResponse(actions=[], note=f"Not asking the LLM: {ns_violation}.", kill_switch=policy.kill_switch)

        from app.k8s_client import to_pod_summary

        raw_pod = self.k8s.get_pod(namespace, pod_name)
        pod = to_pod_summary(raw_pod)
        events = self.k8s.get_pod_events(namespace, pod_name)
        logs = self.k8s.get_pod_logs(namespace, pod_name, pod=raw_pod)
        pod_state = self.cluster.pod_state(namespace, pod_name)
        deployment_name = pod_state.deployment if pod_state is not None else None
        deployment = self.cluster.deployment_state(namespace, deployment_name) if deployment_name else None
        scope = IncidentScope(namespace=namespace, pod=pod_name, deployment=deployment_name)

        result = self.proposer.propose(build_context(pod, events, logs, deployment, self._signals(namespace, deployment_name)), policy)
        records = [self._record_proposal(req, scope, source="llm") for req in result.requests]
        if not records:
            self.store.audit("no_action_proposed", "llm", None, namespace=namespace, pod=pod_name, note=result.note)
        return ProposeResponse(actions=records, note=result.note, kill_switch=policy.kill_switch)

    def _signals(self, namespace: str, workload: str | None, since: float | None = None) -> list[str]:
        if self.lifecycle is None or workload is None:
            return []
        try:
            report = self.lifecycle.snapshot(namespace=namespace)
        except Exception as exc:  # noqa: BLE001 — signals are context, never required
            logger.info("Lifecycle signals unavailable: %s", exc)
            return []
        out = []
        for s in report.signals:
            if s.workload != workload or (since is not None and s.ts < since):
                continue
            out.append(f"{s.kind} pod={s.pod or '-'} {s.detail}".strip())
        return out

    def _record_proposal(
        self, req: ActionRequest, scope: IncidentScope | None, source: str, reverts: str | None = None
    ) -> ActionRecord:
        policy = self.policy
        params = req.params
        namespace = params.get("namespace") if isinstance(params.get("namespace"), str) else ""
        name = params.get("name") if isinstance(params.get("name"), str) else ""
        violations = validate(req, policy, scope)
        target_kind = "Pod" if req.kind == ActionKind.DELETE_POD.value else "Deployment"
        workload = name if target_kind == "Deployment" else (scope.deployment if scope else "") or ""
        preview = None
        if not violations:
            try:
                target = self.cluster.target_state(req.kind, params)
                violations = validate(req, policy, scope, target)
                if target.pod is not None and target.pod.deployment:
                    workload = target.pod.deployment
            except Exception as exc:  # noqa: BLE001
                violations = [f"could not read the target: {exc}"]
        if not violations:
            preview = self.cluster.preview(req.kind, params)
            if not preview.dry_run_ok:
                violations = [f"server-side dry run failed: {preview.dry_run_error}"]
        now = _now()
        record = ActionRecord(
            id=uuid.uuid4().hex[:12],
            created_at=now,
            updated_at=now,
            kind=req.kind,
            params=params,
            namespace=namespace,
            target=name,
            target_kind=target_kind,
            workload=workload,
            incident_pod=f"{scope.namespace}/{scope.pod}" if scope else None,
            rationale=req.rationale,
            source=source,
            reverts_action_id=reverts,
            status=ActionStatus.BLOCKED if violations else ActionStatus.PROPOSED,
            violations=violations,
            preview=preview,
        )
        self.store.save(record)
        if violations:
            self.store.audit("policy_blocked", source, record.id, kind=req.kind, params=params, violations=violations)
        else:
            self.store.audit("proposed", source, record.id, kind=req.kind, params=params, rationale=req.rationale, diff=preview.diff if preview else "")
        return record

    # ------------------------------------------------------------------ decide
    def _get(self, action_id: str) -> ActionRecord:
        record = self.store.get(action_id)
        if record is None:
            raise ActionNotFoundError(f"Action {action_id} was not found")
        return record

    def _scope_for(self, record: ActionRecord) -> IncidentScope:
        pod = record.target if record.kind == ActionKind.DELETE_POD.value else ""
        if record.incident_pod and record.source == "llm":
            pod = record.incident_pod.split("/", 1)[1]
        return IncidentScope(namespace=record.namespace, pod=pod, deployment=record.workload or None)

    def reject(self, action_id: str, actor: str, comment: str | None = None) -> ActionRecord:
        with self._lock:
            record = self._get(action_id)
            if record.status != ActionStatus.PROPOSED:
                raise ActionConflictError(f"Action is {record.status.value}; only proposed actions can be rejected")
            record.status = ActionStatus.REJECTED
            record.decided_by, record.decided_at, record.decision_comment = actor, _now(), comment
            record.updated_at = _now()
            self.store.save(record)
            self.store.audit("rejected", f"human:{actor}", record.id, comment=comment)
            return record

    def approve(self, action_id: str, actor: str, comment: str | None = None) -> ActionRecord:
        self._require_enabled()
        policy = self.policy
        with self._lock:
            record = self._get(action_id)
            if record.status != ActionStatus.PROPOSED:
                raise ActionConflictError(f"Action is {record.status.value}; only proposed actions can be approved")
            if policy.kill_switch:
                self.store.audit("kill_switch_blocked", f"human:{actor}", record.id)
                raise KillSwitchError("The global kill switch is on (ACTIONS_KILL_SWITCH=true); nothing will be executed.")
            age = (_now() - record.created_at).total_seconds()
            if age > self.settings.actions_proposal_ttl_seconds:
                record.status, record.updated_at = ActionStatus.EXPIRED, _now()
                self.store.save(record)
                self.store.audit("expired", "system", record.id, age_seconds=round(age))
                raise ActionConflictError("Proposal expired; ask for a fresh proposal")
            self._check_rate_limits(record, actor)
            req = ActionRequest(kind=record.kind, params=record.params, rationale=record.rationale)
            try:
                target = self.cluster.target_state(record.kind, record.params)
                violations = validate(req, policy, self._scope_for(record), target)
            except Exception as exc:  # noqa: BLE001
                violations = [f"could not re-read the target: {exc}"]
            if violations:
                record.status, record.violations, record.updated_at = ActionStatus.BLOCKED, violations, _now()
                self.store.save(record)
                self.store.audit("policy_blocked_at_approval", "policy", record.id, violations=violations)
                raise ActionConflictError("Policy re-check failed: " + "; ".join(violations))
            record.status = ActionStatus.EXECUTING
            record.decided_by, record.decided_at, record.decision_comment = actor, _now(), comment
            record.updated_at = _now()
            self.store.save(record)
            self.store.audit("approved", f"human:{actor}", record.id, comment=comment)

        try:
            before, after = self.cluster.apply(record.kind, record.params, dry_run=False)
        except ActionExecutionError as exc:
            record.status, record.error, record.updated_at = ActionStatus.FAILED, str(exc), _now()
            self.store.save(record)
            self.store.audit("execution_failed", "system", record.id, error=str(exc))
            return record
        record.executed_at = _now()
        record.status = ActionStatus.VERIFYING
        record.revertible, record.revert_params = self._revert_plan(record, before)
        record.updated_at = _now()
        self.store.save(record)
        self.store.audit("executed", "system", record.id, before=before, after=after)
        if self._verify_in_background:
            threading.Thread(target=self._verify_safe, args=(record.id,), daemon=True).start()
        else:
            self._verify_safe(record.id)
            record = self._get(record.id)
        return record

    def _check_rate_limits(self, record: ActionRecord, actor: str) -> None:
        same = self.store.list(namespace=record.namespace, workload=record.workload, limit=500)
        busy = [r for r in same if r.id != record.id and r.status in IN_FLIGHT]
        if busy:
            self.store.audit("rate_limited", f"human:{actor}", record.id, reason="in_flight", other=busy[0].id)
            raise ActionConflictError(f"Another action ({busy[0].id}) is still running on {record.workload}")
        cooldown = timedelta(seconds=self.settings.actions_cooldown_seconds)
        recent = [r for r in same if r.executed_at is not None and _now() - r.executed_at < cooldown]
        if recent:
            wait = int((cooldown - (_now() - recent[0].executed_at)).total_seconds())
            self.store.audit("rate_limited", f"human:{actor}", record.id, reason="cooldown", wait_seconds=wait)
            raise RateLimitedError(f"{record.workload} was acted on recently; cooldown has {wait}s left")
        hour_ago = _now() - timedelta(hours=1)
        last_hour = [r for r in self.store.list(limit=1000) if r.executed_at is not None and r.executed_at > hour_ago]
        if len(last_hour) >= self.settings.actions_max_per_hour:
            self.store.audit("rate_limited", f"human:{actor}", record.id, reason="hourly_cap")
            raise RateLimitedError(f"Hourly cap of {self.settings.actions_max_per_hour} executed actions reached")

    @staticmethod
    def _revert_plan(record: ActionRecord, before: dict) -> tuple[bool, dict | None]:
        if record.kind == ActionKind.SCALE_DEPLOYMENT.value and isinstance(before.get("replicas"), int):
            return True, {"namespace": record.namespace, "name": record.target, "replicas": before["replicas"]}
        if record.kind == ActionKind.ROLLBACK_DEPLOYMENT.value:
            # Rolling back again returns to the template that was live before.
            return True, {"namespace": record.namespace, "name": record.target}
        return False, None  # a restart or a deleted pod cannot be meaningfully undone

    def propose_revert(self, action_id: str, actor: str) -> ActionRecord:
        self._require_enabled()
        original = self._get(action_id)
        if original.status not in EXECUTED or original.status == ActionStatus.VERIFYING:
            raise ActionConflictError("Only executed and verified actions can be reverted")
        if not original.revertible or original.revert_params is None:
            raise ActionConflictError(f"{original.kind} has no meaningful revert")
        req = ActionRequest(
            kind=original.kind,
            params=dict(original.revert_params),
            rationale=f"Revert of action {original.id} requested by {actor}",
        )
        scope = IncidentScope(namespace=original.namespace, pod="", deployment=original.workload)
        self.store.audit("revert_requested", f"human:{actor}", original.id)
        return self._record_proposal(req, scope, source="revert", reverts=original.id)

    # ------------------------------------------------------------------ verify
    def _verify_safe(self, action_id: str) -> None:
        try:
            self._verify(action_id)
        except Exception as exc:  # noqa: BLE001 — never leave an action stuck in "verifying"
            logger.exception("Verification crashed for %s", action_id)
            record = self._get(action_id)
            record.status, record.error, record.updated_at = ActionStatus.NOT_RESOLVED, f"verification error: {exc}", _now()
            self.store.save(record)
            self.store.audit("verification_error", "system", action_id, error=str(exc))

    def _verify(self, action_id: str) -> None:
        record = self._get(action_id)
        window = float(self.settings.actions_verify_window_seconds)
        stable = float(self.settings.actions_verify_stable_seconds)
        interval = float(self.settings.actions_verify_interval_seconds)
        verification = Verification(started_at=_now(), window_seconds=window, stable_seconds=stable)
        self.store.audit("verification_started", "system", action_id, window_seconds=window, stable_seconds=stable)
        exec_wall = record.executed_at.timestamp() if record.executed_at else time.time()
        start = self._monotonic()
        healthy_since: float | None = None
        last_restarts: dict[str, int] = {}
        reason = ""
        while True:
            elapsed = self._monotonic() - start
            state, unhealthy, restarts = self.cluster.workload_health(record.namespace, record.workload)
            restarted = [p for p, n in restarts.items() if n > last_restarts.get(p, n)]
            last_restarts = restarts
            if state is None:
                healthy = False
                reason = "Deployment disappeared"
                sample = VerificationSample(t=round(elapsed, 1), desired=0, ready=0, available=0, updated=0, healthy=False)
            else:
                healthy = (
                    state.observed_generation >= state.generation
                    and state.updated == state.replicas
                    and state.ready == state.replicas
                    and state.available == state.replicas
                    and not unhealthy
                    and not restarted
                )
                sample = VerificationSample(
                    t=round(elapsed, 1),
                    desired=state.replicas,
                    ready=state.ready,
                    available=state.available,
                    updated=state.updated,
                    unhealthy_pods=unhealthy + [f"{p} (restarted)" for p in restarted],
                    healthy=healthy,
                )
            verification.samples.append(sample)
            if healthy:
                healthy_since = elapsed if healthy_since is None else healthy_since
                if elapsed - healthy_since >= stable:
                    break
            else:
                healthy_since = None
            if elapsed >= window:
                break
            self._sleep(interval)

        resolved = healthy_since is not None and (elapsed - healthy_since) >= stable
        if resolved:
            signals = self._signals(record.namespace, record.workload, since=exec_wall + healthy_since)
            bad = [s for s in signals if s.split(" ", 1)[0] not in EXPECTED_AFTER_ACTION]
            verification.disruption_signals = bad
            if bad:
                resolved = False
                reason = f"workload healthy but {len(bad)} disruption signal(s) fired while it was stable"
            else:
                reason = f"all {sample.desired} replica(s) ready and stable for {stable:.0f}s, no new restarts or disruption signals"
                verification.time_to_recovery_seconds = round(healthy_since, 1)
        elif not reason:
            last = verification.samples[-1]
            reason = (
                f"not healthy within {window:.0f}s: {last.ready}/{last.desired} ready, "
                f"{last.available} available" + (f"; unhealthy: {', '.join(last.unhealthy_pods)}" if last.unhealthy_pods else "")
            )
        verification.metric_anomalies = self._metric_anomalies(record)
        verification.resolved = resolved
        verification.reason = reason
        verification.finished_at = _now()
        record = self._get(action_id)
        record.verification = verification
        record.status = ActionStatus.RESOLVED if resolved else ActionStatus.NOT_RESOLVED
        record.updated_at = _now()
        self.store.save(record)
        self.store.audit(
            "verified_resolved" if resolved else "verified_not_resolved",
            "system",
            action_id,
            reason=reason,
            time_to_recovery_seconds=verification.time_to_recovery_seconds,
            samples=len(verification.samples),
        )

    def _metric_anomalies(self, record: ActionRecord) -> list[str] | None:
        """Informational: Isolation Forest verdict for the workload's pods (needs Prometheus)."""
        if self.scorer is None or self.prometheus is None or not getattr(self.scorer, "loaded", False):
            return None
        try:
            series = self.prometheus.list_pod_series(namespace=record.namespace)
        except Exception:  # noqa: BLE001
            return None
        flagged = []
        for (ns, pod), points in series.items():
            if not pod.startswith(f"{record.workload}-"):
                continue
            try:
                if self.scorer.score_window(ns, pod, points).is_anomaly:
                    flagged.append(pod)
            except Exception:  # noqa: BLE001
                continue
        return flagged

    # ------------------------------------------------------------------ read
    def detail(self, action_id: str) -> ActionDetail:
        record = self._get(action_id)
        return ActionDetail(**record.model_dump(), timeline=self.store.audit_events(action_id=action_id))
