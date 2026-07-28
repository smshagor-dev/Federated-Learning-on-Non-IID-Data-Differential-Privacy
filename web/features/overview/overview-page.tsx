"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { MetricCard } from "@/components/metric-card";
import { StatusPill } from "@/components/status-pill";
import type { AuthSession, Experiment, OverviewData } from "@/types/api";

export function OverviewPage({ data }: { data: OverviewData | undefined }) {
  const router = useRouter();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    const cached = window.localStorage.getItem("fl-platform-session");
    if (!cached) {
      return;
    }
    try {
      setSession(JSON.parse(cached) as AuthSession);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  function handleRefresh() {
    startTransition(() => {
      router.refresh();
    });
  }

  if (!data) {
    return (
      <article className="card overview-hero-card">
        <div className="eyebrow">Backend unavailable</div>
        <h2 className="card-title">Live overview data could not be loaded on July 28, 2026</h2>
        <p className="card-copy">
          This dashboard no longer falls back to synthetic fixtures. The current backend did not return overview data,
          so metrics, experiments, and runs remain unavailable until the API responds again.
        </p>
        <div className="operator-actions">
          <button className="button-secondary" disabled={isPending} onClick={handleRefresh} type="button">
            {isPending ? "Refreshing..." : "Retry live overview"}
          </button>
          <Link className="button-primary" href="/login">
            Review auth session
          </Link>
        </div>
      </article>
    );
  }

  const recentExperiments = data.experiments.slice(0, 5);
  const latestRuns = data.runs.slice(0, 5);

  return (
    <>
      <div className="metric-grid">
        <MetricCard
          label="Total Experiments"
          value={String(data.experiments.length)}
          caption={`${deltaLabel(data.experiments.length, data.projects.length)} active research portfolio breadth.`}
        />
        <MetricCard
          label="Active Runs"
          value={String(data.metrics.running_runs)}
          caption={`${data.metrics.queued_runs} queued and ${data.metrics.paused_runs} paused across the control plane.`}
        />
        <MetricCard
          label="Privacy Posture"
          value={`${data.metrics.system_readiness}%`}
          caption="Platform readiness score only. No combined epsilon or delta is derived here."
        />
        <MetricCard
          label="Audit Activity"
          value={String(data.metrics.recent_audit_events)}
          caption={`Recent journal activity from the ${data.source} overview feed.`}
        />
      </div>

      <div className="double-grid">
        <article className="card overview-hero-card">
          <div className="operator-header">
            <div>
              <div className="eyebrow">Operational snapshot</div>
              <h2 className="card-title">System posture across projects, runs, and reproducibility inputs</h2>
              <p className="card-copy">
                This workspace renders only live Go control-plane data. The page reflects the current platform state
                as of July 28, 2026.
              </p>
            </div>
            <div className="pill-row">
              <span className="pill">Source: {data.source}</span>
              <span className="pill">Projects: {data.metrics.active_projects}</span>
              <span className="pill">Completed runs: {data.metrics.completed_runs}</span>
            </div>
          </div>
          <div className="bar-stack">
            <div className="bar-row">
              <span>Readiness</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${data.metrics.system_readiness}%` }} />
              </div>
              <strong>{data.metrics.system_readiness}%</strong>
            </div>
            <div className="bar-row">
              <span>Run throughput</span>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{
                    width: `${ratioPercent(data.metrics.running_runs + data.metrics.completed_runs, maxTotalRuns(data))}%`,
                  }}
                />
              </div>
              <strong>{data.metrics.running_runs + data.metrics.completed_runs}</strong>
            </div>
            <div className="bar-row">
              <span>Failure pressure</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${ratioPercent(data.metrics.failed_runs, maxTotalRuns(data))}%` }} />
              </div>
              <strong>{data.metrics.failed_runs}</strong>
            </div>
          </div>
          <div className="operator-actions">
            <Link className="button-primary" href="/experiments/new">
              Launch builder
            </Link>
            <button className="button-secondary" disabled={isPending} onClick={handleRefresh} type="button">
              {isPending ? "Refreshing..." : "Refresh overview"}
            </button>
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Operator context</div>
          <h2 className="card-title">Researcher session and recent workflow movement</h2>
          <div className="pill-row">
            <span className="pill">User: {session?.user.display_name ?? "Guest viewer"}</span>
            <span className="pill">Role: {session?.user.role ?? "guest"}</span>
            <span className="pill">Date: July 28, 2026</span>
          </div>
          <div className="timeline-list compact-list">
            {data.activity_feed.slice(0, 5).map((event) => (
              <div className="timeline-item" key={event.id}>
                <div className="timeline-dot" />
                <div>
                  <strong>{event.action}</strong>
                  <div className="muted">
                    {event.actor_email ?? "system"} on {event.resource_type} {event.resource_id ?? "n/a"}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <div className="operator-header">
            <div>
              <div className="eyebrow">Recent experiments</div>
              <h2 className="card-title">Tracked experiment registry</h2>
            </div>
            <Link className="inline-link" href="/experiments/new">
              Open builder
            </Link>
          </div>
          <div className="audit-table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Experiment</th>
                  <th>Project</th>
                  <th>Updated</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {recentExperiments.map((experiment) => (
                  <tr key={experiment.id}>
                    <td>
                      <div>{experiment.name}</div>
                      <div className="muted">{experiment.id}</div>
                    </td>
                    <td>{experiment.project_id}</td>
                    <td>{formatDateTime(experiment.updated_at)}</td>
                    <td>
                      <StatusPill status={statusFromExperiment(experiment)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Recent runs</div>
          <h2 className="card-title">Execution flow at a glance</h2>
          <table className="table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Mode</th>
              </tr>
            </thead>
            <tbody>
              {latestRuns.map((run) => (
                <tr key={run.id}>
                  <td>
                    <Link className="inline-link" href={`/runs/${run.id}`}>
                      {run.id}
                    </Link>
                  </td>
                  <td>
                    <StatusPill status={run.status} />
                  </td>
                  <td>{String(run.config.mode ?? "unknown")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      </div>

      <div className="triple-grid">
        <article className="card">
          <div className="eyebrow">Project radar</div>
          <h2 className="card-title">Active project spaces</h2>
          <div className="project-list">
            {data.projects.slice(0, 3).map((project) => (
              <Link className="project-card" href="/experiments/new" key={project.id}>
                <div className="project-card-top">
                  <strong>{project.name}</strong>
                  <span>{project.id}</span>
                </div>
                <p className="muted">{project.description}</p>
                <div className="pill-row">
                  <span className="pill">Created: {project.created_at.slice(0, 10)}</span>
                </div>
              </Link>
            ))}
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Outcome balance</div>
          <h2 className="card-title">Run distribution</h2>
          <div className="bar-stack">
            <div className="bar-row">
              <span>Running</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${ratioPercent(data.metrics.running_runs, maxTotalRuns(data))}%` }} />
              </div>
              <strong>{data.metrics.running_runs}</strong>
            </div>
            <div className="bar-row">
              <span>Completed</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${ratioPercent(data.metrics.completed_runs, maxTotalRuns(data))}%` }} />
              </div>
              <strong>{data.metrics.completed_runs}</strong>
            </div>
            <div className="bar-row">
              <span>Failed</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${ratioPercent(data.metrics.failed_runs, maxTotalRuns(data))}%` }} />
              </div>
              <strong>{data.metrics.failed_runs}</strong>
            </div>
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Quick paths</div>
          <h2 className="card-title">Common operator moves</h2>
          <ul className="list">
            <li>Open the builder to create experiment configs against the live algorithm descriptors.</li>
            <li>Jump into a run page for lifecycle controls and run-specific privacy telemetry.</li>
            <li>Use the login route before testing role-aware pages that require a bearer session.</li>
          </ul>
        </article>
      </div>
    </>
  );
}

function maxTotalRuns(data: OverviewData) {
  return Math.max(
    1,
    data.metrics.running_runs +
      data.metrics.queued_runs +
      data.metrics.paused_runs +
      data.metrics.completed_runs +
      data.metrics.failed_runs,
  );
}

function ratioPercent(value: number, total: number) {
  return Math.round((value / Math.max(1, total)) * 100);
}

function deltaLabel(experiments: number, projects: number) {
  if (projects <= 0) {
    return "No project coverage yet.";
  }
  return `${projects} active project spaces supporting ${experiments} tracked configs.`;
}

function statusFromExperiment(experiment: Experiment) {
  const mode = String(experiment.config.mode ?? "").toLowerCase();
  if (mode.includes("queue")) {
    return "QUEUED";
  }
  if (mode.includes("pause")) {
    return "PAUSED";
  }
  return "RUNNING";
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}
