"""Data shapes for safe actions: proposals, previews, verification and the audit log."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActionKind(str, Enum):
    RESTART_DEPLOYMENT = "restart_deployment"
    SCALE_DEPLOYMENT = "scale_deployment"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    DELETE_POD = "delete_pod"


ALLOWED_KINDS = frozenset(k.value for k in ActionKind)


class ActionStatus(str, Enum):
    PROPOSED = "proposed"  # passed policy and dry run; waiting for a human
    BLOCKED = "blocked"  # failed policy (or dry run); can never be approved
    REJECTED = "rejected"  # a human said no
    EXPIRED = "expired"  # too old to approve
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    NOT_RESOLVED = "not_resolved"
    FAILED = "failed"  # the API call itself failed


IN_FLIGHT = frozenset({ActionStatus.EXECUTING, ActionStatus.VERIFYING})
EXECUTED = frozenset({ActionStatus.VERIFYING, ActionStatus.RESOLVED, ActionStatus.NOT_RESOLVED})


class ActionRequest(BaseModel):
    """What the LLM asked for. `kind` is a plain string on purpose: it may be hallucinated."""

    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class Preview(BaseModel):
    dry_run_ok: bool = False
    dry_run_error: str | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    diff: str = ""


class VerificationSample(BaseModel):
    t: float  # seconds since execution
    desired: int
    ready: int
    available: int
    updated: int
    unhealthy_pods: list[str] = Field(default_factory=list)
    healthy: bool


class Verification(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    window_seconds: float
    stable_seconds: float
    resolved: bool | None = None
    time_to_recovery_seconds: float | None = None
    reason: str = ""
    samples: list[VerificationSample] = Field(default_factory=list)
    disruption_signals: list[str] = Field(default_factory=list)
    metric_anomalies: list[str] | None = None  # None when Prometheus/model unavailable


class ActionRecord(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    namespace: str = ""
    target: str = ""
    target_kind: str = ""
    workload: str = ""
    incident_pod: str | None = None
    rationale: str = ""
    source: str = "llm"
    reverts_action_id: str | None = None
    status: ActionStatus
    violations: list[str] = Field(default_factory=list)
    preview: Preview | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None
    executed_at: datetime | None = None
    error: str | None = None
    verification: Verification | None = None
    revertible: bool = False
    revert_params: dict[str, Any] | None = None


class AuditEvent(BaseModel):
    id: int
    ts: datetime
    action_id: str | None
    event: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ActionDetail(ActionRecord):
    timeline: list[AuditEvent] = Field(default_factory=list)


class ProposeResponse(BaseModel):
    actions: list[ActionRecord]
    note: str = ""
    kill_switch: bool = False


class DecisionBody(BaseModel):
    actor: str = Field(min_length=1, max_length=80, description="Who is approving or rejecting")
    comment: str | None = Field(default=None, max_length=500)


class ActionsConfigOut(BaseModel):
    enabled: bool
    kill_switch: bool
    allowed_namespaces: list[str]
    denied_namespaces: list[str]
    allowed_actions: list[str]
    scale_min: int
    scale_max: int
    cooldown_seconds: int
    max_per_hour: int
    proposal_ttl_seconds: int
    verify_window_seconds: int


class ActionsListResponse(BaseModel):
    actions: list[ActionRecord]
    count: int


class AuditResponse(BaseModel):
    events: list[AuditEvent]
    count: int
