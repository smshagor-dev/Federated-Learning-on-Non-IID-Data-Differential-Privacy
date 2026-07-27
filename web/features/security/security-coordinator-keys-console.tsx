"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog, type ConfirmDialogInput } from "@/components/confirm-dialog";
import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import {
  listCoordinatorSigningKeysWithToken,
  revokeCoordinatorSigningKeyWithToken,
  rotateCoordinatorSigningKeyWithToken,
} from "@/lib/security-api";
import { formatUnixSeconds } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { CoordinatorSigningKey } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;

type PendingAction = { kind: "rotate" } | { kind: "revoke"; signingKeyId: string; currentStatus: string };

export function SecurityCoordinatorKeysConsole() {
  const session = useStoredSession();
  const token = session?.token;
  const role = session?.user.role;
  const [keys, setKeys] = useState<CoordinatorSigningKey[] | undefined>(undefined);
  const [unavailable, setUnavailable] = useState(false);

  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const [expectedCurrentKeyId, setExpectedCurrentKeyId] = useState("");
  // Real defects found by this slice's live Playwright browser suite:
  // both defaults below previously exceeded the coordinator's own
  // enforced maximums (coordinator_signing_key_registry.hpp's
  // kMaxCoordinatorKeyLifetimeSeconds = 90 days,
  // kMaxGracePeriodSeconds = 1 day), so a real admin submitting the
  // rotation form with its own unmodified defaults always got a real
  // 409 ("requested grace period ... exceeds the maximum allowed
  // 86400s"). No non-browser test caught this because the harness's
  // own Python scenarios never submit the web form's own default
  // values -- only a real form submission does.
  const [newKeyExpiryDays, setNewKeyExpiryDays] = useState("90");
  const [gracePeriodDays, setGracePeriodDays] = useState("1");

  useEffect(() => {
    if (!token) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    async function poll() {
      const result = await listCoordinatorSigningKeysWithToken(token as string, controller.signal);
      if (cancelled) {
        return;
      }
      setUnavailable(result === undefined);
      if (result !== undefined) {
        setKeys(result);
        const active = result.find((key) => key.status === "active");
        setExpectedCurrentKeyId((current) => current || active?.signing_key_id || "");
      }
    }

    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [token]);

  async function runPendingAction(input: ConfirmDialogInput) {
    if (!token || !pendingAction) {
      return;
    }
    setMutationBusy(true);
    setMutationError(null);
    try {
      if (pendingAction.kind === "rotate") {
        const nowUnixS = Math.floor(Date.now() / 1000);
        const expiryDays = Number(newKeyExpiryDays) || 90;
        const graceDays = Number(gracePeriodDays) || 1;
        const result = await rotateCoordinatorSigningKeyWithToken(token, {
          reason: input.reason,
          idempotencyKey: input.idempotencyKey,
          expectedCurrentSigningKeyId: expectedCurrentKeyId,
          newKeyExpiresAtUnixS: nowUnixS + expiryDays * 86_400,
          requestedGracePeriodSeconds: graceDays * 86_400,
        });
        if (!result.accepted) {
          setMutationError(`Rotation rejected: ${result.reason || result.rejection_code}`);
          return;
        }
        setStatusMessage(
          result.idempotent_replay
            ? "Rotation request replayed from a prior attempt (no new key minted)."
            : `Rotated to new key ${result.new_key.signing_key_id}.`,
        );
      } else {
        const result = await revokeCoordinatorSigningKeyWithToken(token, pendingAction.signingKeyId, {
          reason: input.reason,
          idempotencyKey: input.idempotencyKey,
          expectedStatus: pendingAction.currentStatus,
        });
        setKeys((current) => current?.map((key) => (key.signing_key_id === result.key.signing_key_id ? result.key : key)));
        setStatusMessage(
          `Signing key ${pendingAction.signingKeyId} revoked` +
            (result.production_task_issuance_stopped ? " -- task issuance halted until a new key rotates in." : "."),
        );
      }
      setPendingAction(null);
    } catch (mutationErr) {
      setMutationError(mutationErr instanceof Error ? mutationErr.message : "Action failed");
    } finally {
      setMutationBusy(false);
    }
  }

  const canMutate = role === "admin";

  if (!token) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/coordinator-keys" />
        <article className="card security-hero-card">
          <div className="eyebrow">Coordinator signing keys</div>
          <p className="card-copy">Sign in to view coordinator signing-key status.</p>
        </article>
      </div>
    );
  }

  if (unavailable || keys === undefined) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/coordinator-keys" />
        <article className="card security-hero-card">
          <div className="eyebrow">Coordinator signing keys</div>
          <div className="notice">{unavailable ? "Coordinator is not reachable right now." : "Loading..."}</div>
        </article>
      </div>
    );
  }

  const activeKey = keys.find((key) => key.status === "active");

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security/coordinator-keys" />

      <article className="card operator-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Coordinator signing keys</div>
            <h2 className="card-title">Coordinator identity key rotation and revocation</h2>
            <p className="card-copy">
              The coordinator signs every task it issues with this key. Workers verify task authenticity against the
              trusted key bundle -- rotation and revocation directly affect what every worker will accept.
            </p>
          </div>
          <div className="pill-row">
            {activeKey ? <span className="pill">Active: {activeKey.signing_key_id}</span> : <span className="pill">No active key</span>}
          </div>
        </div>
        {statusMessage ? <div className="success-banner">{statusMessage}</div> : null}
        {canMutate ? (
          <div className="operator-actions">
            <button className="button-primary" type="button" onClick={() => setPendingAction({ kind: "rotate" })}>
              Rotate coordinator signing key
            </button>
          </div>
        ) : (
          <div className="muted">Sign in as an admin to rotate or revoke coordinator signing keys.</div>
        )}
      </article>

      <article className="card">
        <div className="audit-table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Key ID</th>
                <th>Status</th>
                <th>Created</th>
                <th>Expires</th>
                <th>Grace period ends</th>
                {canMutate ? <th></th> : null}
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.signing_key_id}>
                  <td>{key.signing_key_id}</td>
                  <td>
                    <SecurityStatusPill status={key.status} />
                  </td>
                  <td>{formatUnixSeconds(key.created_at_unix_s)}</td>
                  <td>{formatUnixSeconds(key.expires_at_unix_s)}</td>
                  <td>{key.grace_period_end_unix_s ? formatUnixSeconds(key.grace_period_end_unix_s) : "n/a"}</td>
                  {canMutate ? (
                    <td>
                      <button
                        className="button-secondary"
                        type="button"
                        disabled={key.status === "revoked"}
                        onClick={() => setPendingAction({ kind: "revoke", signingKeyId: key.signing_key_id, currentStatus: key.status })}
                      >
                        Revoke
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      {pendingAction?.kind === "rotate" ? (
        <ConfirmDialog
          open
          title="Rotate coordinator signing key"
          consequence="A new Ed25519 keypair is generated and becomes active immediately. The previous active key moves to a grace period during which both old- and new-signed tasks are accepted, then expires. This mints a fresh key every time it actually executes -- it is not safely retryable without the idempotency key shown below."
          confirmLabel="Rotate key"
          busy={mutationBusy}
          error={mutationError}
          onCancel={() => {
            setPendingAction(null);
            setMutationError(null);
          }}
          onConfirm={runPendingAction}
        >
          <div className="section-grid">
            <label className="field-card">
              <span className="field-label">Expected current active key ID</span>
              <input
                className="input"
                value={expectedCurrentKeyId}
                onChange={(event) => setExpectedCurrentKeyId(event.target.value)}
                placeholder={activeKey?.signing_key_id ?? "none active"}
              />
            </label>
            <label className="field-card">
              <span className="field-label">New key lifetime (days, max 90)</span>
              <input
                className="input"
                type="number"
                min={1}
                max={90}
                value={newKeyExpiryDays}
                onChange={(event) => setNewKeyExpiryDays(event.target.value)}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Grace period for the old key (days, max 1)</span>
              <input
                className="input"
                type="number"
                min={0}
                max={1}
                value={gracePeriodDays}
                onChange={(event) => setGracePeriodDays(event.target.value)}
              />
            </label>
          </div>
        </ConfirmDialog>
      ) : null}

      {pendingAction?.kind === "revoke" ? (
        <ConfirmDialog
          open
          title={`Revoke coordinator key ${pendingAction.signingKeyId}`}
          consequence="Immediately untrusts this key. If it is the active key, task issuance halts until a new key is rotated in -- workers will reject any task signed after this point until then."
          confirmLabel="Revoke key"
          danger
          busy={mutationBusy}
          error={mutationError}
          onCancel={() => {
            setPendingAction(null);
            setMutationError(null);
          }}
          onConfirm={runPendingAction}
        />
      ) : null}
    </div>
  );
}
