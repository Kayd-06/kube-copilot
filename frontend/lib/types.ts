export type ContainerStateSummary = {
  name: string;
  ready: boolean;
  restart_count: number;
  state: string;
  reason: string | null;
  message: string | null;
  exit_code: number | null;
};

export type PodSummary = {
  name: string;
  namespace: string;
  phase: string;
  reason: string | null;
  message: string | null;
  restart_count: number;
  ready: boolean;
  created_at: string | null;
  node: string | null;
  qos_class: string | null;
  containers: ContainerStateSummary[];
};

export type PodEvent = {
  type: string;
  reason: string;
  message: string;
  count: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  source: string | null;
};

export type PodLogs = {
  current: string;
  previous: string | null;
  container: string | null;
};

export type Diagnosis = {
  summary: string;
  likely_root_cause: string;
  suggested_fix: string;
  kubectl_commands: string[];
  confidence: number;
};

export type Citation = {
  title: string;
  source: string;
  section: string;
  score: number;
  kind?: string;
};

export type UnhealthyPodsResponse = {
  pods: PodSummary[];
  count: number;
};

export type FeatureContribution = {
  name: string;
  value: number;
  zscore: number;
  direction: string;
};

export type MetricSample = {
  ts: number;
  cpu: number;
  memory_mib: number;
  mem_to_limit_ratio: number;
  restarts: number;
};

export type LifecycleSignal = {
  kind: string;
  at: string;
  workload: string;
  pod?: string | null;
  detail: string;
};

export type WorkloadDisruption = {
  namespace: string;
  workload: string;
  signals: LifecycleSignal[];
  pods: string[];
  summary: string;
};

export type AnomalyScore = {
  namespace: string;
  name: string;
  score: number;
  /** Union of the metric and lifecycle detectors. */
  is_anomaly: boolean;
  threshold: number;
  metric_anomaly?: boolean | null;
  detectors?: string[];
  lifecycle_signals?: LifecycleSignal[];
  contributing_features: FeatureContribution[];
  series: MetricSample[];
  warning?: string | null;
};

export type AnomaliesResponse = {
  pods: AnomalyScore[];
  count: number;
  model_loaded: boolean;
  prometheus_unavailable?: boolean;
  disruptions?: WorkloadDisruption[];
  lifecycle_unavailable?: boolean;
  warning?: string | null;
};

export type DiagnoseResponse = {
  pod: PodSummary;
  events: PodEvent[];
  logs: PodLogs;
  diagnosis: Diagnosis;
  citations: Citation[];
  knowledge_unavailable?: boolean;
  anomaly?: AnomalyScore | null;
  anomaly_unavailable?: boolean;
};

export type IncidentSaved = {
  id: string;
  title: string;
  collection: string;
};

// ---- Safe actions ----
export type ActionStatus =
  | "proposed"
  | "blocked"
  | "rejected"
  | "expired"
  | "executing"
  | "verifying"
  | "resolved"
  | "not_resolved"
  | "failed";

export type ActionPreview = {
  dry_run_ok: boolean;
  dry_run_error: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  diff: string;
};

export type VerificationSample = {
  t: number;
  desired: number;
  ready: number;
  available: number;
  updated: number;
  unhealthy_pods: string[];
  healthy: boolean;
};

export type Verification = {
  started_at: string;
  finished_at: string | null;
  window_seconds: number;
  stable_seconds: number;
  resolved: boolean | null;
  time_to_recovery_seconds: number | null;
  reason: string;
  samples: VerificationSample[];
  disruption_signals: string[];
  metric_anomalies: string[] | null;
};

export type AuditEvent = {
  id: number;
  ts: string;
  action_id: string | null;
  event: string;
  actor: string;
  detail: Record<string, unknown>;
};

export type ActionRecord = {
  id: string;
  created_at: string;
  updated_at: string;
  kind: string;
  params: Record<string, unknown>;
  namespace: string;
  target: string;
  target_kind: string;
  workload: string;
  incident_pod: string | null;
  rationale: string;
  source: string;
  reverts_action_id: string | null;
  status: ActionStatus;
  violations: string[];
  preview: ActionPreview | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  executed_at: string | null;
  error: string | null;
  verification: Verification | null;
  revertible: boolean;
  revert_params: Record<string, unknown> | null;
};

export type ActionDetail = ActionRecord & { timeline: AuditEvent[] };

export type ProposeResponse = { actions: ActionRecord[]; note: string; kill_switch: boolean };

export type ActionsConfig = {
  enabled: boolean;
  kill_switch: boolean;
  allowed_namespaces: string[];
  denied_namespaces: string[];
  allowed_actions: string[];
  scale_min: number;
  scale_max: number;
  cooldown_seconds: number;
  max_per_hour: number;
  proposal_ttl_seconds: number;
  verify_window_seconds: number;
};
