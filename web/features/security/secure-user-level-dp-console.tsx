"use client";

import { useEffect, useState } from "react";

import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import {
  getSecureUserDPBudgetWithToken,
  getSecureUserDPHealthWithToken,
  getSecureUserDPStatusWithToken,
  listSecureUserDPRoundsWithToken,
} from "@/lib/security-api";
import { formatBoolean, formatTimestamp, formatUnixSeconds } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { SecureUserDPBudget, SecureUserDPCapability, SecureUserDPHealth, SecureUserDPRound } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;
const ROUNDS_PAGE_LIMIT = 25;

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice, Work Area M: 10 limitation warnings that must be displayed
// prominently -- rendered as a visible list on every load, never a
// tooltip/collapsed disclosure a viewer could miss.
const LIMITATION_WARNINGS: string[] = [
  "Worker clipping is not cryptographically verified -- the coordinator trusts each worker's self-reported clipped update.",
  "A malicious worker may submit an unclipped masked update; nothing in this mechanism detects or prevents that.",
  "No privacy amplification is claimed unless a validated random-sampling mechanism is active -- this deployment assumes q=1 (no amplification).",
  "The signed privacy attestation is evidence of configured worker behavior, not cryptographic proof of correct execution.",
  "This mechanism is honest-client-dependent: it provides no protection against a client that does not follow the protocol.",
  "Dropout resilience is not implemented -- an incomplete cohort aborts the round rather than reconstructing a partial aggregate.",
  "Variable per-user weighting is not supported under this mechanism -- every user's weight is fixed at exactly 1.",
  "Independent privacy and cryptographic reviews of this mechanism have not been completed.",
  "This is an experimental research implementation, not a production-hardened privacy system.",
  "This deployment is not production privacy-ready and must not be treated as satisfying a formal compliance requirement.",
];

function useSecureUserDPHealth() {
  const session = useStoredSession();
  const token = session?.token;
  const [status, setStatus] = useState<SecureUserDPCapability | undefined>();
  const [health, setHealth] = useState<SecureUserDPHealth | undefined>();
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    let cancelled = false;

    async function poll() {
      const [statusResult, healthResult] = await Promise.all([
        getSecureUserDPStatusWithToken(token as string, controller.signal),
        getSecureUserDPHealthWithToken(token as string, controller.signal),
      ]);
      if (cancelled) return;
      setStatus(statusResult);
      setHealth(healthResult);
      setUnavailable(statusResult === undefined && healthResult === undefined);
    }

    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [token]);

  return { token, status, health, unavailable };
}

function BudgetLookup({ token }: { token: string }) {
  const [runId, setRunId] = useState("");
  const [budget, setBudget] = useState<SecureUserDPBudget | undefined>();
  const [state, setState] = useState<"idle" | "loading" | "error" | "ready">("idle");

  async function lookup() {
    if (!runId.trim()) return;
    setState("loading");
    const result = await getSecureUserDPBudgetWithToken(token, runId.trim());
    if (result === undefined) {
      setState("error");
      setBudget(undefined);
      return;
    }
    setBudget(result);
    setState("ready");
  }

  return (
    <article className="card">
      <div className="eyebrow">Budget</div>
      <h3 className="card-title">Per-run epsilon accounting</h3>
      <div className="section-grid">
        <label className="field-card">
          <span className="field-label">Run ID</span>
          <input
            className="input"
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
            placeholder="run-id"
            data-testid="secure-user-dp-budget-run-id"
          />
        </label>
        <button className="button" type="button" onClick={() => void lookup()} data-testid="secure-user-dp-budget-lookup">
          Look up budget
        </button>
      </div>
      {state === "error" ? (
        <div className="notice" data-testid="secure-user-dp-budget-error">
          No budget record available for that run (or access is denied for your role).
        </div>
      ) : null}
      {state === "ready" && budget ? (
        <dl className="definition-grid" data-testid="secure-user-dp-budget-result">
          <div>
            <dt>Budget configured</dt>
            <dd>{formatBoolean(budget.budget_configured)}</dd>
          </div>
          <div>
            <dt>Epsilon spent</dt>
            <dd>{budget.epsilon_spent}</dd>
          </div>
          <div>
            <dt>Epsilon budget</dt>
            <dd>{budget.epsilon_budget}</dd>
          </div>
          <div>
            <dt>Epsilon remaining</dt>
            <dd>{budget.epsilon_remaining}</dd>
          </div>
          <div>
            <dt>Target delta</dt>
            <dd>{budget.target_delta}</dd>
          </div>
          <div>
            <dt>Rounds committed</dt>
            <dd>{budget.rounds_committed}</dd>
          </div>
        </dl>
      ) : null}
    </article>
  );
}

// Work Area N: the Privacy Round Explorer -- cursor-paginated, filtered
// by run_id (the only filter the underlying API supports; the task's
// fuller filter list is not all backed by real query params yet, see
// docs/secure-user-level-operations-audit.md's scope statement).
function RoundExplorer({ token }: { token: string }) {
  const [runId, setRunId] = useState("");
  const [rounds, setRounds] = useState<SecureUserDPRound[]>([]);
  const [cursor, setCursor] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "error" | "ready" | "empty">("idle");

  async function load(afterCursor: string, replace: boolean) {
    if (!runId.trim()) return;
    setState("loading");
    const page = await listSecureUserDPRoundsWithToken(token, {
      runId: runId.trim(),
      afterCursor: afterCursor || undefined,
      limit: ROUNDS_PAGE_LIMIT,
    });
    if (page === undefined) {
      setState("error");
      return;
    }
    setRounds((current) => (replace ? page.rounds : [...current, ...page.rounds]));
    setCursor(page.next_cursor);
    setState(replace && page.rounds.length === 0 ? "empty" : "ready");
  }

  return (
    <article className="card">
      <div className="eyebrow">Privacy Round Explorer</div>
      <h3 className="card-title">Committed accounting steps for a run</h3>
      <div className="section-grid">
        <label className="field-card">
          <span className="field-label">Run ID</span>
          <input
            className="input"
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
            placeholder="run-id"
            data-testid="secure-user-dp-rounds-run-id"
          />
        </label>
        <button
          className="button"
          type="button"
          onClick={() => {
            setRounds([]);
            void load("", true);
          }}
          data-testid="secure-user-dp-rounds-search"
        >
          Search rounds
        </button>
      </div>
      {state === "error" ? (
        <div className="notice" data-testid="secure-user-dp-rounds-error">
          Round history is not reachable right now (or access is denied for your role).
        </div>
      ) : null}
      {state === "empty" ? (
        <div className="notice" data-testid="secure-user-dp-rounds-empty">
          No committed rounds found for that run.
        </div>
      ) : null}
      {rounds.length > 0 ? (
        <div className="audit-table-wrap">
          <table className="table" data-testid="secure-user-dp-rounds-table">
            <thead>
              <tr>
                <th>Round</th>
                <th>Epsilon after round</th>
                <th>Target delta</th>
                <th>Noise multiplier</th>
                <th>Clipping bound</th>
                <th>Clients</th>
                <th>Committed at</th>
              </tr>
            </thead>
            <tbody>
              {rounds.map((round) => (
                <tr key={round.round_id}>
                  <td>{round.round_id}</td>
                  <td>{round.epsilon_after_round}</td>
                  <td>{round.target_delta}</td>
                  <td>{round.noise_multiplier}</td>
                  <td>{round.clipping_bound}</td>
                  <td>{round.num_clients}</td>
                  <td>{formatUnixSeconds(round.committed_at_unix_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {cursor ? (
        <button className="button" type="button" onClick={() => void load(cursor, false)} data-testid="secure-user-dp-rounds-load-more">
          Load more
        </button>
      ) : null}
    </article>
  );
}

export function SecureUserLevelDPConsole() {
  const { token, status, health, unavailable } = useSecureUserDPHealth();

  if (!token) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/secure-aggregation/privacy" />
        <article className="card security-hero-card">
          <div className="eyebrow">Secure user-level DP privacy runtime</div>
          <p className="card-copy">Sign in to view the secure user-level DP privacy runtime.</p>
        </article>
      </div>
    );
  }

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security/secure-aggregation/privacy" />

      <article className="card security-hero-card" data-testid="secure-user-dp-limitations">
        <div className="eyebrow">Mandatory trust limitations</div>
        <h2 className="card-title">This mechanism is experimental and honest-client-dependent</h2>
        <ul className="limitation-list">
          {LIMITATION_WARNINGS.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      </article>

      {unavailable ? (
        <div className="notice" data-testid="secure-user-dp-degraded">
          Coordinator is unavailable or this deployment has no secure user-level DP capability configured.
        </div>
      ) : null}

      <article className="card" data-testid="secure-user-dp-capability">
        <div className="eyebrow">Capability</div>
        <h3 className="card-title">Mechanism</h3>
        {status ? (
          <dl className="definition-grid">
            <div>
              <dt>Available</dt>
              <dd>{formatBoolean(status.available)}</dd>
            </div>
            <div>
              <dt>Provider</dt>
              <dd>{status.provider}</dd>
            </div>
            <div>
              <dt>Adjacency model</dt>
              <dd>{status.adjacency_model}</dd>
            </div>
            <div>
              <dt>Sampling assumption</dt>
              <dd>{status.sampling_assumption}</dd>
            </div>
            <div>
              <dt>Sensitivity formula</dt>
              <dd>{status.sensitivity_formula}</dd>
            </div>
            <div>
              <dt>Noise placement</dt>
              <dd>{status.noise_placement}</dd>
            </div>
            <div>
              <dt>Fixed weight</dt>
              <dd>{status.fixed_weight}</dd>
            </div>
          </dl>
        ) : (
          <p className="card-copy muted">Capability information is not available.</p>
        )}
      </article>

      <article className="card" data-testid="secure-user-dp-health">
        <div className="eyebrow">Runtime health</div>
        <h3 className="card-title">Component status</h3>
        {health ? (
          <>
            <div className="pill-row">
              <SecurityStatusPill status={health.provider_status} />
              <SecurityStatusPill status={health.noise_provider_status} />
              <SecurityStatusPill status={health.accountant_status} />
              <SecurityStatusPill status={health.ledger_status} />
              <SecurityStatusPill status={health.event_journal_status} />
            </div>
            <dl className="definition-grid">
              <div>
                <dt>Active runs using this mechanism</dt>
                <dd>{health.active_runs_with_user_level_dp}</dd>
              </div>
              <div>
                <dt>Reconciliation required</dt>
                <dd>{formatBoolean(health.reconciliation_required)}</dd>
              </div>
              <div>
                <dt>Last successful round</dt>
                <dd>{formatTimestamp(health.last_successful_round_at)}</dd>
              </div>
              <div>
                <dt>Degraded reason</dt>
                <dd>{health.degraded_reason || "none"}</dd>
              </div>
              <div>
                <dt>Checked at</dt>
                <dd>{formatUnixSeconds(health.checked_at_unix_s)}</dd>
              </div>
            </dl>
          </>
        ) : (
          <p className="card-copy muted">Runtime health is not available.</p>
        )}
      </article>

      <BudgetLookup token={token} />
      <RoundExplorer token={token} />
    </div>
  );
}
