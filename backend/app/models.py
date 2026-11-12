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


class Citation(BaseModel):
    title: str
    source: str
    section: str
    score: float
    kind: str = "runbook"


class UnhealthyPodsResponse(BaseModel):
    pods: list[PodSummary]
    count: int


class FeatureContribution(BaseModel):
    name: str
    value: float
    zscore: float
    direction: str


class MetricSample(BaseModel):
    ts: float
    cpu: float
    memory_mib: float
    mem_to_limit_ratio: float
    restarts: float


class AnomalyScore(BaseModel):
    namespace: str
    name: str
    score: float
    is_anomaly: bool
    threshold: float
    contributing_features: list[FeatureContribution] = Field(default_factory=list)
    series: list[MetricSample] = Field(default_factory=list)
    warning: str | None = None


class AnomaliesResponse(BaseModel):
    pods: list[AnomalyScore] = Field(default_factory=list)
    count: int = 0
    model_loaded: bool = True
    prometheus_unavailable: bool = False
    warning: str | None = None


class DiagnoseResponse(BaseModel):
    pod: PodSummary
    events: list[PodEvent]
    logs: PodLogs
    diagnosis: Diagnosis
    citations: list[Citation] = Field(default_factory=list)
    knowledge_unavailable: bool = False
    anomaly: AnomalyScore | None = None
    anomaly_unavailable: bool = False


class HealthResponse(BaseModel):
    status: str
    service: str = "kube-copilot"
    version: str


class IncidentCreate(BaseModel):
    pod_name: str
    namespace: str
    reason: str | None = None
    root_cause: str
    fix: str
    notes: str | None = None


class IncidentSaved(BaseModel):
    id: str
    title: str
    collection: str = "incidents"


class KnowledgeHit(BaseModel):
    title: str
    source: str
    section: str
    score: float
    kind: str
    text: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeHit] = Field(default_factory=list)
    knowledge_unavailable: bool = False
    warning: str | None = None
