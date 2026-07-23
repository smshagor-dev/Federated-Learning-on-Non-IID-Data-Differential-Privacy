import type { AuditEvent, Experiment, OverviewData, Project, Run, RunDashboardData } from "@/types/api";

const projects: Project[] = [
  {
    id: "proj-demo-1",
    name: "Non-IID Stability Suite",
    description: "Comparative evaluation across client drift, privacy, and convergence regimes.",
    created_at: "2026-07-22T10:00:00Z",
  },
  {
    id: "proj-demo-2",
    name: "Personalization Frontier",
    description: "Ditto, Per-FedAvg, and FedSAM pilot planning.",
    created_at: "2026-07-22T11:30:00Z",
  },
];

const experiments: Experiment[] = [
  {
    id: "exp-demo-1",
    project_id: "proj-demo-1",
    name: "CIFAR10 DP Comparison",
    description: "FedAvg, FedProx, and SCAFFOLD under user-level DP.",
    config: { dataset: "CIFAR10", rounds: 50, privacy_mode: "user_level_dp" },
    created_at: "2026-07-22T12:00:00Z",
    updated_at: "2026-07-22T12:00:00Z",
  },
  {
    id: "exp-demo-2",
    project_id: "proj-demo-2",
    name: "Personalization Dry Run",
    description: "Backbone-plus-head personalization planning.",
    config: { dataset: "FEMNIST", rounds: 30, algorithm: "ditto" },
    created_at: "2026-07-22T12:30:00Z",
    updated_at: "2026-07-22T12:40:00Z",
  },
];

const runs: Run[] = [
  {
    id: "run-demo-1",
    experiment_id: "exp-demo-1",
    status: "RUNNING",
    config: { current_round: 18, target_clients: 8, mode: "synchronous", rounds: 50, privacy_mode: "user_level_dp" },
    created_at: "2026-07-22T13:00:00Z",
    updated_at: "2026-07-22T13:22:00Z",
  },
  {
    id: "run-demo-2",
    experiment_id: "exp-demo-2",
    status: "PAUSED",
    config: { current_round: 7, target_clients: 12, mode: "deadline_based_semi_synchronous", rounds: 30, privacy_mode: "hybrid_dp" },
    created_at: "2026-07-22T14:10:00Z",
    updated_at: "2026-07-22T15:05:00Z",
  },
  {
    id: "run-demo-3",
    experiment_id: "exp-demo-1",
    status: "COMPLETED",
    config: { current_round: 50, target_clients: 8, mode: "synchronous", rounds: 50, privacy_mode: "user_level_dp" },
    created_at: "2026-07-21T09:00:00Z",
    updated_at: "2026-07-21T11:35:00Z",
  },
];

const auditEvents: AuditEvent[] = [
  {
    id: "audit-demo-3",
    timestamp: "2026-07-22T15:06:00Z",
    actor_email: "researcher@fl-platform.dev",
    actor_role: "researcher",
    action: "run.transition",
    resource_type: "run",
    resource_id: "run-demo-2",
    outcome: "success",
    details: { status: "PAUSED" },
  },
  {
    id: "audit-demo-2",
    timestamp: "2026-07-22T13:22:00Z",
    actor_email: "service@fl-platform.dev",
    actor_role: "service",
    action: "run.create",
    resource_type: "run",
    resource_id: "run-demo-1",
    outcome: "success",
    details: { experiment_id: "exp-demo-1" },
  },
  {
    id: "audit-demo-1",
    timestamp: "2026-07-22T12:40:00Z",
    actor_email: "researcher@fl-platform.dev",
    actor_role: "researcher",
    action: "experiment.create",
    resource_type: "experiment",
    resource_id: "exp-demo-2",
    outcome: "success",
    details: { project_id: "proj-demo-2" },
  },
];

export function getDemoOverview(): OverviewData {
  return {
    projects,
    experiments,
    runs,
    metrics: {
      running_runs: 1,
      queued_runs: 0,
      paused_runs: 1,
      completed_runs: 1,
      failed_runs: 0,
      active_projects: 2,
      recent_audit_events: auditEvents.length,
      system_readiness: 81,
    },
    activity_feed: auditEvents,
    source: "demo",
  };
}

export function getDemoRun(runId: string): Run | undefined {
  return runs.find((run) => run.id === runId);
}

export function getDemoRunDashboard(runId: string): RunDashboardData | undefined {
  const run = getDemoRun(runId);
  if (!run) {
    return undefined;
  }
  const currentRound = Number(run.config.current_round ?? 0);
  const targetRounds = Number(run.config.rounds ?? 0);
  const targetClients = Number(run.config.target_clients ?? 0);
  const progress = targetRounds > 0 ? Math.min(100, Math.floor((currentRound * 100) / targetRounds)) : 0;
  return {
    run,
    metrics: {
      current_round: currentRound,
      target_rounds: targetRounds,
      target_clients: targetClients,
      progress_percent: progress,
      accuracy_percent: Math.min(96, 52 + Math.floor(progress / 2)),
      loss_improvement_percent: Math.min(92, 34 + Math.floor(progress / 2)),
      privacy_budget_percent: Math.min(96, 18 + currentRound * 3),
      worker_throughput_percent: Math.min(96, 45 + targetClients * 4),
    },
    audit_events: auditEvents.filter((event) => event.resource_id === runId),
    signals: [
      "Demo feed updated on July 22, 2026.",
      `Execution mode: ${String(run.config.mode ?? "unknown")}.`,
      `Privacy mode: ${String(run.config.privacy_mode ?? "pending live telemetry")}.`,
    ],
    source: "demo",
  };
}
