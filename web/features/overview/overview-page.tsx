"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { MetricCard } from "@/components/metric-card";
import { StatusPill } from "@/components/status-pill";
import type { AuthSession, OverviewData } from "@/types/api";

export function OverviewPage({ data }: { data: OverviewData }) {
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

  return (
    <>
      <div className="hero-grid">
        <article className="card overview-hero-card">
          <h2 className="card-title">Operational Snapshot</h2>
          <p className="card-copy">
            This dashboard pulls live data from the Go control-plane when available and falls back to deterministic demo data when the API is offline.
          </p>
          <div className="pill-row">
            <span className="pill">Data source: {data.source}</span>
            <span className="pill">System readiness: {data.metrics.system_readiness}%</span>
            <span className="pill">Recent audit events: {data.metrics.recent_audit_events}</span>
          </div>
          <div className="operator-actions" style={{ marginTop: 18 }}>
            <Link className="button-primary" href="/experiments/new">
              Launch builder
            </Link>
            <button className="button-secondary" disabled={isPending} onClick={handleRefresh} type="button">
              {isPending ? "Refreshing..." : "Refresh overview"}
            </button>
          </div>
        </article>
        <article className="card">
          <h2 className="card-title">Operator Context</h2>
          <div className="pill-row">
            <span className="pill">User: {session?.user.display_name ?? "Guest viewer"}</span>
            <span className="pill">Role: {session?.user.role ?? "guest"}</span>
            <span className="pill">Date: July 22, 2026</span>
          </div>
          <div className="timeline-list compact-list" style={{ marginTop: 18 }}>
            {data.activity_feed.map((event) => (
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

      <div className="metric-grid">
        <MetricCard label="Projects" value={String(data.metrics.active_projects)} caption="Organized research workspaces." />
        <MetricCard label="Experiments" value={String(data.experiments.length)} caption="Config snapshots currently tracked." />
        <MetricCard label="Running Runs" value={String(data.metrics.running_runs)} caption={`Queued runs: ${data.metrics.queued_runs}`} />
        <MetricCard label="Completed Runs" value={String(data.metrics.completed_runs)} caption={`Paused runs: ${data.metrics.paused_runs}`} />
      </div>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Project Radar</h2>
          <div className="project-list">
            {data.projects.map((project) => (
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
          <h2 className="card-title">Recent Runs</h2>
          <table className="table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Mode</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((run) => (
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

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Capacity and Privacy Readiness</h2>
          <div className="bar-stack">
            <div className="bar-row">
              <span>Operational readiness</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${data.metrics.system_readiness}%` }} />
              </div>
              <strong>{data.metrics.system_readiness}%</strong>
            </div>
            <div className="bar-row">
              <span>Audit coverage</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${Math.min(100, data.metrics.recent_audit_events * 12)}%` }} />
              </div>
              <strong>{data.metrics.recent_audit_events} events</strong>
            </div>
            <div className="bar-row">
              <span>Failed run pressure</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${Math.min(100, data.metrics.failed_runs * 20)}%` }} />
              </div>
              <strong>{data.metrics.failed_runs}</strong>
            </div>
          </div>
        </article>
        <article className="card">
          <h2 className="card-title">Quick Paths</h2>
          <ul className="list">
            <li>Open the builder to launch a fresh experiment and run from one flow.</li>
            <li>Jump into a run ID to pause, resume, cancel, or refresh execution state.</li>
            <li>Use the login page first if you need researcher or admin write access.</li>
          </ul>
        </article>
      </div>
    </>
  );
}
