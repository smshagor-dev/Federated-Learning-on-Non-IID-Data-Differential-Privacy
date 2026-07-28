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

export type ResearchExperimentSummary = {
  experiment_id: string;
  display_name: string;
  research_question: string;
  dataset_id: string;
  model_id: string;
  algorithm_id: string;
  privacy_mode: string;
  secure_aggregation_enabled: boolean;
  secure_aggregation_provider?: string;
  adaptive_clipping_enabled: boolean;
  declared_seed_count: number;
  current_state: string;
  successful_run_count: number;
  failed_run_count: number;
  canceled_run_count: number;
  blocked_run_count: number;
  created_at: string;
  updated_at: string;
  degraded?: boolean;
  degraded_reason?: string;
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

// the Coordinator Runtime phase: the live federated coordinator's own run state, distinct
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

// the Algorithm Expansion phase: algorithm metadata (go/internal/algorithms), for the
// experiment builder to render algorithm-specific fields without
// hardcoding per-algorithm form logic — see
// features/builder/experiment-builder.tsx.
export type AlgorithmConfigField = {
  name: string;
  type: "float" | "int" | "bool" | "string";
  default: unknown;
  description: string;
};

export type AlgorithmDescriptor = {
  name: string;
  display_name: string;
  description: string;
  supports_personalization: boolean;
  config_fields: AlgorithmConfigField[];
};

// the Algorithm Expansion phase: model registry (go/internal/models, mirroring
// python/src/fl_platform/models/model_registry.py).
export type ModelStatus = "DRAFT" | "VALIDATED" | "ACTIVE" | "DEPRECATED" | "ARCHIVED";

export type ModelEntry = {
  name: string;
  version: string;
  architecture_name: string;
  input_channels: number;
  num_classes: number;
  normalization: string;
  parameter_count: number;
  state_dict_schema_hash: string;
  aggregatable_parameter_names: string[];
  personalizable_parameter_names: string[];
  supported_datasets: string[];
  supported_algorithms: string[];
  checkpoint_reference: string;
  checksum: string;
  status: ModelStatus;
  created_at: number;
  updated_at: number;
};

// the Algorithm Expansion phase: dataset registry (go/internal/datasets, mirroring
// python/src/fl_platform/datasets/dataset_registry.py).
export type DatasetStatus = "DRAFT" | "VALIDATED" | "ACTIVE" | "DEPRECATED" | "ARCHIVED";

export type DatasetEntry = {
  dataset_id: string;
  name: string;
  version: string;
  task_type: string;
  num_classes: number;
  input_shape: number[];
  train_sample_count: number;
  eval_sample_count: number;
  normalization: string;
  storage_reference: string;
  checksum: string;
  license_metadata: string;
  status: DatasetStatus;
  created_at: number;
  updated_at: number;
};

export type PartitionEntry = {
  partition_id: string;
  dataset_id: string;
  strategy: "iid" | "dirichlet" | "pathological";
  seed: number;
  num_clients: number;
  alpha?: number;
  classes_per_client?: number;
  minimum_client_samples: number;
  client_sample_counts: Record<string, number>;
  manifest_checksum: string;
  created_at: number;
};

// the Algorithm Expansion phase: personalization/fairness (go/internal/application/fairness.go,
// mirroring python/src/fl_platform/personalization/metrics.py).
export type PersonalizationRecord = {
  client_id: string;
  round_id: number;
  algorithm: string;
  global_local_accuracy: number;
  personalized_local_accuracy: number;
  global_local_loss: number;
  personalized_local_loss: number;
  sample_count: number;
  personalized_improvement: number;
  personalized_model_version: number;
  recorded_at: string;
  has_personalized_model: boolean;
};

export type PersonalizationMetrics = {
  global_accuracy: number;
  mean_personalized_accuracy: number;
  median_personalized_accuracy: number;
  p10_personalized_accuracy: number;
  p25_personalized_accuracy: number;
  p75_personalized_accuracy: number;
  p90_personalized_accuracy: number;
  worst_client_accuracy: number;
  best_client_accuracy: number;
  fairness_gap: number;
  mean_improvement_over_global: number;
  median_improvement_over_global: number;
  std_dev_personalized_accuracy: number;
  fraction_clients_improved: number;
  coefficient_of_variation: number | null;
  jain_fairness_index: number | null;
  client_count: number;
  excluded_client_count: number;
  excluded_reasons: string[];
};

// Privacy Engineering phase (go/internal/coordinator/client.go, mirroring
// fl.privacy.v1's identically-named messages). CRITICAL PRIVACY RULE: the
// three mechanisms' epsilon/delta values must never be summed, averaged,
// or otherwise combined into one number anywhere in this UI — every
// component consuming these types renders them as separate cards/rows,
// never arithmetic on top of each other.
export type PrivacyMetricsSnapshot = {
  run_id: string;
  round_id: number;
  has_sample_level: boolean;
  sample_epsilon: number;
  sample_delta: number;
  has_user_level: boolean;
  user_epsilon: number;
  user_delta: number;
  has_clipping: boolean;
  clipping_epsilon: number;
  clipping_delta: number;
  current_clip_value: number;
};

export type SampleLevelLedgerEntry = {
  run_id: string;
  round_id: number;
  client_id: string;
  epsilon: number;
  delta: number;
  noise_multiplier: number;
  sample_rate: number;
  steps: number;
  accountant: string;
  recorded_at: string;
  entry_id: string;
};

export type UserLevelLedgerEntry = {
  run_id: string;
  round_id: number;
  epsilon: number;
  delta: number;
  noise_multiplier: number;
  clipping_bound: number;
  num_clients: number;
};

export type AdaptiveClippingLedgerEntry = {
  run_id: string;
  round_id: number;
  epsilon: number;
  delta: number;
  clip_value: number;
  observed_over_threshold_fraction: number;
};

export type PrivacyLedger = {
  sample_level_entries: SampleLevelLedgerEntry[];
  user_level_entries: UserLevelLedgerEntry[];
  clipping_entries: AdaptiveClippingLedgerEntry[];
  next_page_token?: string;
};

// *_budget_remaining is absent/undefined when that mechanism's
// epsilon_budget is unset — never confuse that with 0 (budget
// exhausted). See go/internal/coordinator/client.go's PrivacyProjection
// doc comment.
export type PrivacyProjection = {
  has_sample_level: boolean;
  sample_current_epsilon: number;
  sample_projected_next_epsilon: number;
  sample_budget_remaining?: number;
  has_user_level: boolean;
  user_current_epsilon: number;
  user_projected_next_epsilon: number;
  user_budget_remaining?: number;
  has_clipping: boolean;
  clipping_current_epsilon: number;
  clipping_projected_next_epsilon: number;
  clipping_budget_remaining?: number;
};

// go/internal/privacy/compatibility.go (hand-mirrored from
// python/src/fl_platform/privacy/compatibility.py).
export type CompatibilityEntry = {
  status: "supported" | "experimental" | "unsupported" | "deferred";
  reason: string;
};

export type CompatibilityMatrixRow = {
  algorithm: string;
  sample_level: CompatibilityEntry;
  user_level: CompatibilityEntry;
  hybrid: CompatibilityEntry;
};

export type AlgorithmSummary = {
  run_id: string;
  algorithm: string;
  reporting_client_count: number;
  fairness: PersonalizationMetrics;
};

// Web Security Center, Event Centralization, and Security CI slice
// (go/internal/transport/httpapi/security_overview.go,
// go/internal/coordinator/security_client.go,
// go/internal/observability/security_event.go,
// go/internal/observability/security_audit_journal.go). Every field here
// mirrors a Go JSON tag exactly, same discipline as every other block in
// this file. Fields marked optional may be genuinely absent (never sent)
// under role-based redaction (VIEWER/RESEARCHER without read_detailed) —
// callers must treat "undefined" as "not authorized to see this", not as
// "empty".

export type TransportStatus = {
  transport_mode: string;
  mutual_tls_enforced: boolean;
  checked_at_unix_s: number;
};

export type SecurityTrustModel = {
  active_coordinator_signing_key_id: string;
  trusted_coordinator_key_count: number;
  trusted_key_bundle_version: number;
  registered_worker_count: number;
  worker_signing_key_total_count: number;
  checked_at_unix_s: number;
};

// Full (ADMIN/RESEARCHER) shape. VIEWER receives only { worker_id,
// registration_status } — see viewForRole in security_handlers.go.
export type SecurityWorker = {
  worker_id: string;
  registration_status: string;
  certificate_identity?: string;
  certificate_fingerprint?: string;
  signing_key_id?: string;
  software_version?: string;
  build_id?: string;
  created_at_unix_s?: number;
  updated_at_unix_s?: number;
  expires_at_unix_s?: number;
  suspended_at_unix_s?: number;
  revoked_at_unix_s?: number;
  revocation_reason?: string;
};

export type WorkerLifecycleResult = {
  identity: SecurityWorker;
  changed: boolean;
  leases_canceled: number;
};

export type WorkerSigningKey = {
  worker_id: string;
  signing_key_id: string;
  public_key_fingerprint: string;
  status: string;
  created_at_unix_s: number;
  activated_at_unix_s: number;
  expires_at_unix_s: number;
  grace_period_start_unix_s: number;
  grace_period_end_unix_s: number;
  rotated_from_key_id: string;
  rotated_to_key_id: string;
  revoked_at_unix_s: number;
  revocation_reason: string;
  registration_source: string;
};

export type WorkerSigningKeyRevocationResult = {
  key: WorkerSigningKey;
  changed: boolean;
  worker_suspended: boolean;
};

export type CoordinatorSigningKey = {
  signing_key_id: string;
  public_key_fingerprint: string;
  status: string;
  created_at_unix_s: number;
  expires_at_unix_s: number;
  grace_period_end_unix_s: number;
  rotated_from_key_id: string;
  rotated_to_key_id: string;
  revoked_at_unix_s: number;
  revocation_reason: string;
};

export type RotateCoordinatorSigningKeyResult = {
  accepted: boolean;
  reason: string;
  rejection_code: string;
  new_key: CoordinatorSigningKey;
  previous_key: CoordinatorSigningKey;
  idempotent_replay: boolean;
};

export type RevokeCoordinatorSigningKeyResult = {
  key: CoordinatorSigningKey;
  changed: boolean;
  production_task_issuance_stopped: boolean;
  idempotent_replay: boolean;
};

// Full (ADMIN) shape. Redacted (non-detailed) responses omit
// safe_details/reason_code/request_id/trace_id -- see redactSecurityEvent
// in security_handlers.go.
export type SecurityEventRecord = {
  event_id: string;
  event_type: string;
  severity: "INFO" | "WARNING" | "HIGH" | "CRITICAL";
  timestamp: string;
  source_service: string;
  source_component?: string;
  actor_type: string;
  safe_actor_id: string;
  subject_type: string;
  safe_subject_id: string;
  worker_id: string;
  run_id?: string;
  round_id?: number;
  task_id?: string;
  safe_signing_key_id?: string;
  request_id?: string;
  trace_id?: string;
  outcome: "ACCEPTED" | "REJECTED" | "COMPLETED" | "FAILED" | "BLOCKED" | "CANCELED";
  reason_code?: string;
  safe_details?: Record<string, string>;
};

export type SecurityEventsResponse = {
  events: SecurityEventRecord[];
};

// Full (ADMIN) shape. Redacted responses omit reason/request_id/trace_id
// -- see redactSecurityAuditRecord in security_handlers.go.
export type SecurityAuditRecord = {
  record_id: string;
  timestamp: string;
  actor_role?: string;
  safe_actor_id?: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  outcome: string;
  reason?: string;
  request_id?: string;
  trace_id?: string;
  safe_details?: Record<string, string>;
};

export type SecurityAuditResponse = {
  records: SecurityAuditRecord[];
  next_cursor: string;
};

export type SecurityEventSource = {
  source_service: string;
  last_event_at?: string;
  lag_seconds?: number;
  record_count: number;
  recovered_line_count: number;
  corrupted: boolean;
  retention_active: boolean;
  batches_accepted?: number;
  batches_rejected?: number;
  distinct_workers_seen?: number;
  // Work Package P: true once a source that has reported at least one
  // event falls behind the fixed staleness threshold
  // (go/internal/transport/httpapi/security_overview.go's
  // staleSecurityEventSourceThresholdSeconds) -- never true for a
  // source with no last_event_at yet, since "never reported" and
  // "reported, then went quiet" are different failure signals.
  stale: boolean;
};

export type SecurityEventSourcesResponse = {
  sources: SecurityEventSource[];
  checked_at_unix_s: number;
};

export type SecurityJournalHealth = {
  size_records: number;
  last_record_at?: string;
  lag_seconds?: number;
  recovered_lines: number;
  corrupted: boolean;
  retention_active: boolean;
};

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice (docs/secure-user-level-operations-audit.md): wire types for
// GET /api/v1/secure-aggregation/privacy/* (go/internal/transport/
// httpapi/secure_user_level_privacy.go). Never carries a clear update,
// individual norm, clipping factor, individual weight, noise tensor/
// state, or masked payload -- see that file's own header comment.
export type SecureUserDPCapability = {
  available: boolean;
  provider: string;
  adjacency_model: string;
  sampling_assumption: string;
  sensitivity_formula: string;
  noise_placement: string;
  fixed_weight: number;
  trust_limitations: string[];
};

export type SecureUserDPHealth = {
  capability: SecureUserDPCapability;
  provider_status: string;
  noise_provider_status: string;
  accountant_status: string;
  ledger_status: string;
  event_journal_status: string;
  last_successful_round_at?: string;
  active_runs_with_user_level_dp: number;
  reconciliation_required: boolean;
  degraded_reason?: string;
  checked_at_unix_s: number;
};

export type SecureUserDPBudget = {
  run_id: string;
  budget_configured: boolean;
  epsilon_spent: number;
  epsilon_budget: number;
  epsilon_remaining: number;
  target_delta: number;
  rounds_committed: number;
};

export type SecureUserDPRound = {
  run_id: string;
  round_id: number;
  epsilon_after_round: number;
  target_delta: number;
  noise_multiplier: number;
  clipping_bound: number;
  num_clients: number;
  committed_at_unix_s: number;
};

export type SecureUserDPRoundsPage = {
  rounds: SecureUserDPRound[];
  next_cursor: string;
};

export type SecurityFeatureAvailability = {
  secure_aggregation_available: boolean;
  worker_attestation_available: boolean;
  verifiable_client_clipping_available: boolean;
  byzantine_robustness_available: boolean;
  central_coordinator_observes_updates: boolean;
};

export type SecurityOverview = {
  transport: TransportStatus & { coordinator_available: boolean };
  worker_identities: {
    active: number;
    suspended: number;
    revoked: number;
    expired: number;
    certificate_expiry_warnings: number;
  };
  worker_signing_keys: {
    active: number;
    grace_period: number;
    expired: number;
    revoked: number;
    recent_rotation_failures: number;
  };
  coordinator_keys: {
    active_key_id?: string;
    active_key_expires_at_unix_s: number;
    grace_period_key_id?: string;
    grace_period_end_unix_s: number;
    historical_expired_count: number;
    historical_revoked_count: number;
    trusted_bundle_version: number;
    bundle_healthy: boolean;
  };
  signed_messages: {
    accepted: number;
    rejected: number;
    signature_failures: number;
    payload_hash_failures: number;
    replay_rejections: number;
    sequence_rejections: number;
  };
  privacy_records: {
    accepted: number;
    rejected: number;
    monotonicity_violations: number;
    configuration_mismatches: number;
  };
  tasks: {
    signed: number;
    signing_failures: number;
    verification_failures: number;
    replay_rejections: number;
    duplicate_execution_blocks: number;
    reissues: number;
  };
  event_journal: SecurityJournalHealth;
  audit_journal: SecurityJournalHealth;
  recent_critical_event_count: number;
  recent_high_severity_event_count: number;
  feature_availability: SecurityFeatureAvailability;
  generated_at_unix_s: number;
};
