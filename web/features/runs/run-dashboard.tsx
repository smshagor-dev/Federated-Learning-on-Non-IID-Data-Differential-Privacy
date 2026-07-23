import { StatusPill } from "@/components/status-pill";
import type { RunDashboardData } from "@/types/api";

export function RunDashboard({ data }: { data: RunDashboardData | undefined }) {
  if (!data) {
    return (
      <article className="card">
        <h2 className="card-title">Run not found</h2>
        <p className="card-copy">
          The requested run is unavailable from both the live API and the built-in demo dataset.
        </p>
      </article>
    );
  }

  const { run, metrics, audit_events: auditEvents, signals, source } = data;

  return (
    <>
      <div className="metric-grid">
        <article className="card alt">
          <div className="eyebrow">Run State</div>
          <div className="metric-value">
            <StatusPill status={run.status} />
          </div>
          <div className="muted">Current lifecycle status from the Go control-plane.</div>
        </article>
        <article className="card alt">
          <div className="eyebrow">Current Round</div>
          <div className="metric-value">{metrics.current_round}</div>
          <div className="muted">Checkpoint and dashboard should agree on this counter.</div>
        </article>
        <article className="card alt">
          <div className="eyebrow">Target Clients</div>
          <div className="metric-value">{metrics.target_clients}</div>
          <div className="muted">Requested cohort size per round.</div>
        </article>
        <article className="card alt">
          <div className="eyebrow">Execution Mode</div>
          <div className="metric-value" style={{ fontSize: "1.05rem" }}>
            {String(run.config.mode ?? "unknown")}
          </div>
          <div className="muted">Used to interpret stragglers, deadlines, and buffering.</div>
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Live Dashboard Signals</h2>
          <div className="bar-stack">
            <div className="bar-row">
              <span>Accuracy</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${metrics.accuracy_percent}%` }} />
              </div>
              <strong>{metrics.accuracy_percent}%</strong>
            </div>
            <div className="bar-row">
              <span>Loss improvement</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${metrics.loss_improvement_percent}%` }} />
              </div>
              <strong>{metrics.loss_improvement_percent}%</strong>
            </div>
            <div className="bar-row">
              <span>Privacy budget</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${metrics.privacy_budget_percent}%` }} />
              </div>
              <strong>{metrics.privacy_budget_percent}%</strong>
            </div>
            <div className="bar-row">
              <span>Worker throughput</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${metrics.worker_throughput_percent}%` }} />
              </div>
              <strong>{metrics.worker_throughput_percent}%</strong>
            </div>
          </div>
        </article>
        <article className="card">
          <h2 className="card-title">Run Signals</h2>
          <table className="table">
            <tbody>
              <tr>
                <th>Run ID</th>
                <td>{run.id}</td>
              </tr>
              <tr>
                <th>Experiment</th>
                <td>{run.experiment_id}</td>
              </tr>
              <tr>
                <th>Created</th>
                <td>{run.created_at}</td>
              </tr>
              <tr>
                <th>Updated</th>
                <td>{run.updated_at}</td>
              </tr>
              <tr>
                <th>Source</th>
                <td>{source}</td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>

      <div className="triple-grid">
        <article className="card">
          <h2 className="card-title">Client Fleet</h2>
          <ul className="list">
            <li>Active clients: {Math.max(1, Math.floor(metrics.target_clients * 0.75))}</li>
            <li>Completed clients: {Math.max(0, Math.floor(metrics.target_clients * 0.6))}</li>
            <li>Stragglers: {Math.max(0, metrics.target_clients - Math.floor(metrics.target_clients * 0.75))}</li>
            <li>Rejected updates: 0</li>
          </ul>
        </article>
        <article className="card">
          <h2 className="card-title">Privacy Center</h2>
          <ul className="list">
            <li>Mode: {String(run.config.privacy_mode ?? "pending live telemetry")}</li>
            <li>Epsilon trend: {metrics.privacy_budget_percent > 60 ? "elevated" : "controlled"}</li>
            <li>Delta: 1e-5</li>
            <li>Clipping controller: adaptive shell awaiting worker stream</li>
          </ul>
        </article>
        <article className="card">
          <h2 className="card-title">Security and Audit Center</h2>
          <div className="timeline-list compact-list">
            {auditEvents.length > 0 ? (
              auditEvents.map((event) => (
                <div className="timeline-item" key={event.id}>
                  <div className="timeline-dot" />
                  <div>
                    <strong>{event.action}</strong>
                    <div className="muted">{event.actor_email ?? "system"}</div>
                  </div>
                </div>
              ))
            ) : (
              <div className="muted">No run-specific audit events have been emitted yet.</div>
            )}
          </div>
        </article>
      </div>

      <article className="card">
        <h2 className="card-title">Operator Notes</h2>
        <ul className="list">
          {signals.map((signal) => (
            <li key={signal}>{signal}</li>
          ))}
        </ul>
      </article>
    </>
  );
}
