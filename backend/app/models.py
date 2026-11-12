"""Pydantic models for the Kube Copilot API."""

from datetime import datetime

from pydantic import BaseModel, Field


class ContainerStateSummary(BaseModel):
    name: str
    ready: bool
    restart_count: int
    state: str
    reason: str | None = None
    message: str | None = None
    exit_code: int | None = None


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str
    reason: str | None = None
    message: str | None = None
    restart_count: int = 0
    ready: bool = False
    created_at: datetime | None = None
    node: str | None = None
    qos_class: str | None = None
    containers: list[ContainerStateSummary] = Field(default_factory=list)


class PodEvent(BaseModel):
    type: str
    reason: str
    message: str
    count: int = 1
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    source: str | None = None


class PodLogs(BaseModel):
    current: str = ""
    previous: str | None = None
    container: str | None = None


class Diagnosis(BaseModel):
    summary: str
    likely_root_cause: str
    suggested_fix: str
    kubectl_commands: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class UnhealthyPodsResponse(BaseModel):
    pods: list[PodSummary]
    count: int


class DiagnoseResponse(BaseModel):
    pod: PodSummary
    events: list[PodEvent]
    logs: PodLogs
    diagnosis: Diagnosis


class HealthResponse(BaseModel):
    status: str
    service: str = "kube-copilot"
    version: str
