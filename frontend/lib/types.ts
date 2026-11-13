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
