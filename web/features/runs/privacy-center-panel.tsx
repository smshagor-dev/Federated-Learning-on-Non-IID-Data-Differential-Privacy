"use client";

import { useEffect, useState } from "react";

import { getPrivacyLedgerWithToken, getPrivacyMetricsWithToken, getPrivacyProjectionWithToken } from "@/lib/api";
import type { PrivacyLedger, PrivacyMetricsSnapshot, PrivacyProjection } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;

function formatEpsilon(value: number): string {
  return `ε ≈ ${value.toFixed(3)}`;
}

function formatDelta(value: number): string {
  return `δ = ${value.toExponential(1)}`;
}

function formatBudgetRemaining(value: number | undefined): string {
  // undefined means epsilon_budget was never configured for this
  // mechanism — never render that as "0" or "exhausted", which would
  // falsely suggest a budget is in effect.
  return value === undefined ? "no budget configured" : `${value.toFixed(3)} remaining`;
}

// Privacy Engineering phase: the run dashboard's real Privacy Center —
// see docs/privacy-ledger.md. Mirrors PersonalizationPanel's polling/
// loading-state pattern exactly. CRITICAL PRIVACY RULE: sample-level,
// user-level, and adaptive-clipping mechanisms are always rendered as
// three independent sections — this component performs no arithmetic
// across their epsilon/delta values anywhere, and never presents a
// single combined "privacy score".
export function PrivacyCenterPanel({ runId, token }: { runId: string; token: string | undefined }) {
  const [metrics, setMetrics] = useState<PrivacyMetricsSnapshot | undefined>(undefined);
  const [ledger, setLedger] = useState<PrivacyLedger | undefined>(undefined);
  const [projection, setProjection] = useState<PrivacyProjection | undefined>(undefined);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;
    async function poll() {
      const [metricsResult, ledgerResult, projectionResult] = await Promise.all([
        getPrivacyMetricsWithToken(token as string, runId),
        getPrivacyLedgerWithToken(token as string, runId),
        getPrivacyProjectionWithToken(token as string, runId),
      ]);
      if (cancelled) {
        return;
      }
      setUnavailable(metricsResult === undefined && ledgerResult === undefined && projectionResult === undefined);
      setMetrics(metricsResult);
      setLedger(ledgerResult);
      setProjection(projectionResult);
    }
    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId, token]);

  if (!token) {
    return (
      <article className="card">
        <div className="eyebrow">Privacy Center</div>
        <p className="card-copy">Sign in to view this run&apos;s differential privacy accounting.</p>
      </article>
    );
  }

  if (unavailable) {
    return (
      <article className="card">
        <div className="eyebrow">Privacy Center</div>
        <div className="notice">
          Coordinator is not reachable right now, so privacy accounting cannot be loaded. Local run bookkeeping is
          unaffected.
        </div>
      </article>
    );
  }

  const isPrivate = metrics && (metrics.has_sample_level || metrics.has_user_level || metrics.has_clipping);

  return (
    <article className="card">
      <div className="eyebrow">Privacy Center</div>
      <h3 className="card-title">Differential privacy accounting</h3>
      {!isPrivate ? (
        <div className="muted">
          This run is not configured for differential privacy — no sample-level, user-level, or adaptive-clipping
          mechanism is active.
        </div>
      ) : (
        <>
          <div className="pill-row">
            {metrics?.has_sample_level ? (
              <span className="pill">
                Sample-level (worst client): {formatEpsilon(metrics.sample_epsilon)}, {formatDelta(metrics.sample_delta)}
              </span>
            ) : null}
            {metrics?.has_user_level ? (
              <span className="pill">
                User-level: {formatEpsilon(metrics.user_epsilon)}, {formatDelta(metrics.user_delta)}
              </span>
            ) : null}
            {metrics?.has_clipping ? (
              <span className="pill">
                Adaptive clipping: {formatEpsilon(metrics.clipping_epsilon)}, {formatDelta(metrics.clipping_delta)}
              </span>
            ) : null}
            {metrics?.has_clipping || metrics?.has_user_level ? (
              <span className="pill">Current clip bound: {metrics.current_clip_value.toFixed(3)}</span>
            ) : null}
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            These three values protect different neighboring relations (one training example, one client&apos;s
            complete round contribution, and the clip-bound statistic, respectively) and are never combined into a
            single number.
          </div>

          {projection && (projection.has_sample_level || projection.has_user_level || projection.has_clipping) ? (
            <div style={{ marginTop: 16 }}>
              <h4 className="card-title" style={{ fontSize: "0.95rem" }}>
                One-round-ahead projection
              </h4>
              <table className="table">
                <thead>
                  <tr>
                    <th>Mechanism</th>
                    <th>Current ε</th>
                    <th>Projected next ε</th>
                    <th>Budget remaining</th>
                  </tr>
                </thead>
                <tbody>
                  {projection.has_sample_level ? (
                    <tr>
                      <td>Sample-level</td>
                      <td>{projection.sample_current_epsilon.toFixed(3)}</td>
                      <td>{projection.sample_projected_next_epsilon.toFixed(3)}</td>
                      <td>{formatBudgetRemaining(projection.sample_budget_remaining)}</td>
                    </tr>
                  ) : null}
                  {projection.has_user_level ? (
                    <tr>
                      <td>User-level</td>
                      <td>{projection.user_current_epsilon.toFixed(3)}</td>
                      <td>{projection.user_projected_next_epsilon.toFixed(3)}</td>
                      <td>{formatBudgetRemaining(projection.user_budget_remaining)}</td>
                    </tr>
                  ) : null}
                  {projection.has_clipping ? (
                    <tr>
                      <td>Adaptive clipping</td>
                      <td>{projection.clipping_current_epsilon.toFixed(3)}</td>
                      <td>{projection.clipping_projected_next_epsilon.toFixed(3)}</td>
                      <td>{formatBudgetRemaining(projection.clipping_budget_remaining)}</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          ) : null}

          {ledger && ledger.user_level_entries.length > 0 ? (
            <div style={{ marginTop: 16, overflowX: "auto" }}>
              <h4 className="card-title" style={{ fontSize: "0.95rem" }}>
                User-level ledger (per round)
              </h4>
              <table className="table">
                <thead>
                  <tr>
                    <th>Round</th>
                    <th>ε</th>
                    <th>δ</th>
                    <th>Noise multiplier</th>
                    <th>Clip bound</th>
                    <th>Clients</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.user_level_entries.map((entry) => (
                    <tr key={`${entry.run_id}-${entry.round_id}`}>
                      <td>{entry.round_id}</td>
                      <td>{entry.epsilon.toFixed(3)}</td>
                      <td>{entry.delta.toExponential(1)}</td>
                      <td>{entry.noise_multiplier.toFixed(2)}</td>
                      <td>{entry.clipping_bound.toFixed(3)}</td>
                      <td>{entry.num_clients}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {ledger && ledger.sample_level_entries.length > 0 ? (
            <div style={{ marginTop: 16, overflowX: "auto" }}>
              <h4 className="card-title" style={{ fontSize: "0.95rem" }}>
                Sample-level ledger (per client, per round)
              </h4>
              <table className="table">
                <thead>
                  <tr>
                    <th>Round</th>
                    <th>Client</th>
                    <th>ε</th>
                    <th>δ</th>
                    <th>Accountant</th>
                    <th>Steps</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.sample_level_entries.map((entry) => (
                    <tr key={entry.entry_id}>
                      <td>{entry.round_id}</td>
                      <td>{entry.client_id}</td>
                      <td>{entry.epsilon.toFixed(3)}</td>
                      <td>{entry.delta.toExponential(1)}</td>
                      <td>{entry.accountant}</td>
                      <td>{entry.steps}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      )}
    </article>
  );
}
