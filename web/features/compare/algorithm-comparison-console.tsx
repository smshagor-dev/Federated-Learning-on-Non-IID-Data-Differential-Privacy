"use client";

import { useEffect, useState, useTransition } from "react";

import { getAlgorithmSummaryWithToken } from "@/lib/api";
import type { AlgorithmSummary, AuthSession } from "@/types/api";

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

// the Algorithm Expansion phase: side-by-side comparison of personalization/fairness
// statistics across runs (potentially running different algorithms) —
// docs/algorithm-expansion-architecture.md's algorithm comparison view. Each
// summary is fetched independently, so one unreachable/unknown run
// doesn't block the others from rendering.
export function AlgorithmComparisonConsole() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [runIdInput, setRunIdInput] = useState("");
  const [runIds, setRunIds] = useState<string[]>([]);
  const [summaries, setSummaries] = useState<Record<string, AlgorithmSummary | "unavailable" | undefined>>({});
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

  function addRun() {
    const runId = runIdInput.trim();
    if (!runId || runIds.includes(runId)) {
      return;
    }
    setRunIds((current) => [...current, runId]);
    setRunIdInput("");
    if (session?.token) {
      startTransition(async () => {
        const summary = await getAlgorithmSummaryWithToken(session.token, runId);
        setSummaries((current) => ({ ...current, [runId]: summary ?? "unavailable" }));
      });
    }
  }

  function removeRun(runId: string) {
    setRunIds((current) => current.filter((id) => id !== runId));
    setSummaries((current) => {
      const next = { ...current };
      delete next[runId];
      return next;
    });
  }

  return (
    <>
      <article className="card">
        <div className="eyebrow">Algorithm comparison</div>
        <h2 className="card-title">Compare fairness across runs</h2>
        <p className="card-copy">
          Add run IDs to compare their algorithm, reporting client count, and personalization fairness statistics
          side by side.
        </p>
        {!session?.token ? (
          <div className="notice">Sign in from the auth page first to fetch run summaries.</div>
        ) : null}
        <div className="action-row" style={{ marginTop: 12 }}>
          <input
            className="input"
            placeholder="run-id"
            value={runIdInput}
            onChange={(event) => setRunIdInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                addRun();
              }
            }}
          />
          <button className="button-primary" disabled={isPending || !runIdInput.trim()} onClick={addRun} type="button">
            Add run
          </button>
        </div>
      </article>

      {runIds.length === 0 ? (
        <article className="card">
          <div className="muted">Add at least one run ID above to see its algorithm-summary.</div>
        </article>
      ) : (
        <div className="triple-grid">
          {runIds.map((runId) => {
            const summary = summaries[runId];
            return (
              <article className="card" key={runId}>
                <div className="operator-header">
                  <h3 className="card-title">{runId}</h3>
                  <button className="button-secondary danger-button" onClick={() => removeRun(runId)} type="button">
                    Remove
                  </button>
                </div>
                {summary === undefined ? (
                  <div className="muted">Loading...</div>
                ) : summary === "unavailable" ? (
                  <div className="notice">Run not found, or coordinator unreachable.</div>
                ) : (
                  <>
                    <div className="pill-row">
                      <span className="pill">Algorithm: {summary.algorithm}</span>
                      <span className="pill">Reporting clients: {summary.reporting_client_count}</span>
                    </div>
                    {summary.fairness.client_count > 0 ? (
                      <ul className="list">
                        <li>Global accuracy: {percent(summary.fairness.global_accuracy)}</li>
                        <li>Mean personalized: {percent(summary.fairness.mean_personalized_accuracy)}</li>
                        <li>Fairness gap: {percent(summary.fairness.fairness_gap)}</li>
                        <li>Worst client: {percent(summary.fairness.worst_client_accuracy)}</li>
                      </ul>
                    ) : (
                      <div className="muted">No personalization data reported for this run.</div>
                    )}
                  </>
                )}
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}
