export type Project = {
  id: string;
  name: string;
  description: string;
  created_at: string;
};

export type Experiment = {
  id: string;
  project_id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type RunStatus =
  | "CREATED"
  | "QUEUED"
  | "RUNNING"
  | "PAUSED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELED";

export type RunAction = "start" | "resume" | "pause" | "cancel";

export type Run = {
  id: string;
  experiment_id: string;
  status: RunStatus;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type AuditEvent = {
  id: string;
  timestamp: string;
  actor_id?: string;
  actor_email?: string;
  actor_role?: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  outcome: string;
  details?: Record<string, unknown>;
};

export type OverviewMetrics = {
  running_runs: number;
  queued_runs: number;
  paused_runs: number;
  completed_runs: number;
  failed_runs: number;
  active_projects: number;
  recent_audit_events: number;
  system_readiness: number;
};

export type OverviewData = {
  projects: Project[];
  experiments: Experiment[];
  runs: Run[];
  metrics: OverviewMetrics;
  activity_feed: AuditEvent[];
  source: "live" | "demo";
};

export type RunDashboardMetrics = {
  current_round: number;
  target_rounds: number;
  target_clients: number;
  progress_percent: number;
  accuracy_percent: number;
  loss_improvement_percent: number;
  privacy_budget_percent: number;
  worker_throughput_percent: number;
};

export type RunDashboardData = {
  run: Run;
  metrics: RunDashboardMetrics;
  audit_events: AuditEvent[];
  signals: string[];
  source: "live" | "demo";
};

export type AuthRole = "admin" | "researcher" | "viewer" | "service";

export type AuthUser = {
  id: string;
  email: string;
  display_name: string;
  role: AuthRole;
  created_at: string;
  last_login_at: string;
  capabilities: string[];
};

export type AuthSession = {
  token: string;
  expires_at: string;
  user: AuthUser;
  capabilities: string[];
};

// Milestone 3: the live federated coordinator's own run state, distinct
// from the Run bookkeeping type above (project/experiment scheduling
// metadata) — see docs/go-coordinator-integration.md for why the Go API
// keeps these as separate resources under /api/v1/coordinator/runs/.
export type CoordinatorRunState =
  | "CREATED"
  | "WAITING_FOR_CLIENTS"
  | "RUNNING"
  | "AGGREGATING"
  | "EVALUATING"
  | "CHECKPOINTING"
  | "PAUSED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELED";

export type CoordinatorRunSnapshot = {
  run_id: string;
  state: CoordinatorRunState;
  current_round: number;
  max_rounds: number;
  model_version: string;
  algorithm: string;
  registered_workers: number;
  healthy_workers: number;
};

export type CoordinatorHealth = {
  status: string;
};

export type CoordinatorEvent = {
  event_id: string;
  run_id: string;
  round_id: number;
  type: string;
  client_id?: string;
  worker_id?: string;
  model_version?: string;
  timestamp: string;
  trace_id?: string;
};
