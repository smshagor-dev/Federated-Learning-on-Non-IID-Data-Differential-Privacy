"use client";

import { useEffect, useState } from "react";

import { getFairnessWithToken, getPersonalizationRecordsWithToken } from "@/lib/api";
import type { PersonalizationMetrics, PersonalizationRecord } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

// the Algorithm Expansion phase: personalized-run dashboard section — global vs.
// personalized comparison, fairness gap, worst/best client, per-client
// table. Additive to the existing run dashboard (RunDashboard) since a
// FedAvg/FedProx/SCAFFOLD/FedSAM run legitimately has no personalization
// data at all — see docs/fairness-metrics.md.
export function PersonalizationPanel({ runId, token }: { runId: string; token: string | undefined }) {
  const [fairness, setFairness] = useState<PersonalizationMetrics | undefined>(undefined);
  const [records, setRecords] = useState<PersonalizationRecord[] | undefined>(undefined);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;
    async function poll() {
      const [fairnessResult, recordsResult] = await Promise.all([
        getFairnessWithToken(token as string, runId),
        getPersonalizationRecordsWithToken(token as string, runId),
      ]);
      if (cancelled) {
        return;
      }
      setUnavailable(fairnessResult === undefined && recordsResult === undefined);
      setFairness(fairnessResult);
      setRecords(recordsResult);
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
        <div className="eyebrow">Personalization &amp; fairness</div>
        <p className="card-copy">Sign in to view personalized-model accuracy and fairness statistics for this run.</p>
      </article>
    );
  }

  if (unavailable) {
    return (
      <article className="card">
        <div className="eyebrow">Personalization &amp; fairness</div>
        <div className="notice">
          Coordinator is not reachable right now, so personalization data cannot be loaded. Local run bookkeeping is
          unaffected.
        </div>
      </article>
    );
  }

  const hasPersonalization = (fairness?.client_count ?? 0) > 0;

  return (
    <article className="card">
      <div className="eyebrow">Personalization &amp; fairness</div>
      <h3 className="card-title">Global vs. personalized accuracy</h3>
      {!fairness || !hasPersonalization ? (
        <div className="muted">
          No personalized-model metrics reported for this run yet. This is normal for FedAvg/FedProx/SCAFFOLD/FedSAM
          runs, or a Ditto/Per-FedAvg run before any client has submitted a result.
          {fairness && fairness.excluded_client_count > 0 ? (
            <div className="muted" style={{ marginTop: 8 }}>
              {fairness.excluded_client_count} client record(s) excluded: {fairness.excluded_reasons.join("; ")}
            </div>
          ) : null}
        </div>
      ) : (
        <>
          <div className="pill-row">
            <span className="pill">Global accuracy: {percent(fairness.global_accuracy)}</span>
            <span className="pill">Mean personalized: {percent(fairness.mean_personalized_accuracy)}</span>
            <span className="pill">Worst client: {percent(fairness.worst_client_accuracy)}</span>
            <span className="pill">Best client: {percent(fairness.best_client_accuracy)}</span>
            <span className="pill">Fairness gap: {percent(fairness.fairness_gap)}</span>
            <span className="pill">Reporting clients: {fairness.client_count}</span>
            {fairness.jain_fairness_index !== null ? (
              <span className="pill">Jain index: {fairness.jain_fairness_index.toFixed(3)}</span>
            ) : null}
          </div>
          <div className="bar-stack" style={{ marginTop: 12 }}>
            <div className="bar-row">
              <span>p10</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${fairness.p10_personalized_accuracy * 100}%` }} />
              </div>
              <strong>{percent(fairness.p10_personalized_accuracy)}</strong>
            </div>
            <div className="bar-row">
              <span>Median</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${fairness.median_personalized_accuracy * 100}%` }} />
              </div>
              <strong>{percent(fairness.median_personalized_accuracy)}</strong>
            </div>
            <div className="bar-row">
              <span>p90</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${fairness.p90_personalized_accuracy * 100}%` }} />
              </div>
              <strong>{percent(fairness.p90_personalized_accuracy)}</strong>
            </div>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            {(fairness.fraction_clients_improved * 100).toFixed(0)}% of clients improved over the global model.
          </div>
        </>
      )}

      {records && records.length > 0 ? (
        <div style={{ marginTop: 16, overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Client</th>
                <th>Algorithm</th>
                <th>Global acc.</th>
                <th>Personalized acc.</th>
                <th>Improvement</th>
                <th>Samples</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.client_id}>
                  <td>{record.client_id}</td>
                  <td>{record.algorithm}</td>
                  <td>{percent(record.global_local_accuracy)}</td>
                  <td>{record.has_personalized_model ? percent(record.personalized_local_accuracy) : "—"}</td>
                  <td>{record.has_personalized_model ? percent(record.personalized_improvement) : "—"}</td>
                  <td>{record.sample_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </article>
  );
}
