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
