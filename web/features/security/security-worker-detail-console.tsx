"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog, type ConfirmDialogInput } from "@/components/confirm-dialog";
import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import {
  getSecurityWorkerWithToken,
  listSecurityEventsWithToken,
  listWorkerSigningKeysWithToken,
  mutateWorkerLifecycleWithToken,
  revokeWorkerSigningKeyWithToken,
  type WorkerLifecycleAction,
} from "@/lib/security-api";
import { formatTimestamp, formatUnixSeconds } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { SecurityEventRecord, SecurityWorker, WorkerSigningKey } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;
const RECENT_EVENT_SCAN_LIMIT = 200;

type PendingAction =
  | { kind: "lifecycle"; action: WorkerLifecycleAction }
  | { kind: "revoke-key"; signingKeyId: string };

const LIFECYCLE_COPY: Record<WorkerLifecycleAction, { title: string; consequence: string; confirmLabel: string; danger: boolean }> = {
  suspend: {
    title: "Suspend worker",
    consequence:
      "The worker will be rejected on its next signed message and any active task leases will be canceled. Suspension is reversible via Activate.",
    confirmLabel: "Suspend worker",
    danger: false,
  },
  activate: {
    title: "Activate worker",
    consequence: "The worker will be allowed to resume sending signed messages and receiving tasks.",
    confirmLabel: "Activate worker",
    danger: false,
  },
  revoke: {
    title: "Revoke worker",
    consequence:
      "This is not reversible from this console: a revoked worker's identity is permanently untrusted. The worker will need to be re-registered with a new certificate to participate again.",
    confirmLabel: "Revoke worker",
    danger: true,
  },
};

export function SecurityWorkerDetailConsole({ workerId }: { workerId: string }) {
  const session = useStoredSession();
  const token = session?.token;
  const role = session?.user.role;
  const [identity, setIdentity] = useState<SecurityWorker | undefined>(undefined);
  const [signingKeys, setSigningKeys] = useState<WorkerSigningKey[] | undefined>(undefined);
  const [recentEvents, setRecentEvents] = useState<SecurityEventRecord[]>([]);
  const [unavailable, setUnavailable] = useState(false);

  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    async function poll() {
      const [identityResult, keysResult, eventsResult] = await Promise.all([
        getSecurityWorkerWithToken(token as string, workerId, controller.signal),
        listWorkerSigningKeysWithToken(token as string, workerId, controller.signal),
        listSecurityEventsWithToken(token as string, { limit: RECENT_EVENT_SCAN_LIMIT }, controller.signal),
      ]);
      if (cancelled) {
        return;
      }
      setUnavailable(identityResult === undefined);
      setIdentity(identityResult);
      setSigningKeys(keysResult);
      setRecentEvents((eventsResult ?? []).filter((event) => event.worker_id === workerId));
    }

    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [token, workerId]);

  async function runPendingAction(input: ConfirmDialogInput) {
    if (!token || !pendingAction) {
      return;
    }
    setMutationBusy(true);
    setMutationError(null);
    try {
      if (pendingAction.kind === "lifecycle") {
        const result = await mutateWorkerLifecycleWithToken(token, workerId, pendingAction.action, {
          reason: input.reason,
          idempotencyKey: input.idempotencyKey,
        });
        setIdentity(result.identity);
        setStatusMessage(
          `${LIFECYCLE_COPY[pendingAction.action].confirmLabel} succeeded` +
            (result.leases_canceled > 0 ? ` (${result.leases_canceled} lease(s) canceled).` : "."),
        );
      } else {
        const result = await revokeWorkerSigningKeyWithToken(token, workerId, pendingAction.signingKeyId, {
          reason: input.reason,
          idempotencyKey: input.idempotencyKey,
        });
        setSigningKeys((current) =>
          current?.map((key) => (key.signing_key_id === result.key.signing_key_id ? result.key : key)),
        );
        setStatusMessage(
          `Signing key ${pendingAction.signingKeyId} revoked` +
            (result.worker_suspended ? " (worker auto-suspended)." : "."),
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
        <SecuritySubNav current="/security/workers" />
        <article className="card security-hero-card">
          <div className="eyebrow">Worker detail</div>
          <p className="card-copy">Sign in to view this worker&apos;s identity and signing-key detail.</p>
        </article>
      </div>
    );
  }

  if (unavailable || !identity) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/workers" />
        <article className="card security-hero-card">
          <div className="eyebrow">Worker detail</div>
          <div className="notice">
            {unavailable ? `Unable to load worker ${workerId}.` : "Loading worker detail..."}
          </div>
        </article>
      </div>
    );
  }

  const pendingCopy =
    pendingAction?.kind === "lifecycle"
      ? LIFECYCLE_COPY[pendingAction.action]
      : pendingAction
        ? {
            title: `Revoke signing key ${pendingAction.signingKeyId}`,
            consequence:
              "The worker will no longer be able to sign messages with this key. If this is the worker's only active key, the worker will be automatically suspended until a new key is registered.",
            confirmLabel: "Revoke signing key",
            danger: true,
          }
        : null;

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security/workers" />

      <article className="card operator-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Worker identity</div>
            <h2 className="card-title">{identity.worker_id}</h2>
            <p className="card-copy">
              {identity.certificate_identity ? `Certificate: ${identity.certificate_identity}` : "Aggregate view for this role."}
            </p>
          </div>
          <div className="pill-row">
            <SecurityStatusPill status={identity.registration_status} />
          </div>
        </div>
        {identity.certificate_fingerprint !== undefined ? (
          <div className="section-grid">
            <div className="field-card">
              <span className="field-label">Certificate fingerprint</span>
              {identity.certificate_fingerprint || "n/a"}
            </div>
            <div className="field-card">
              <span className="field-label">Signing key</span>
              {identity.signing_key_id || "n/a"}
            </div>
            <div className="field-card">
              <span className="field-label">Software version / build</span>
              {identity.software_version || "n/a"} / {identity.build_id || "n/a"}
            </div>
            <div className="field-card">
              <span className="field-label">Registered</span>
              {formatUnixSeconds(identity.created_at_unix_s)}
            </div>
            <div className="field-card">
              <span className="field-label">Certificate expires</span>
              {formatUnixSeconds(identity.expires_at_unix_s)}
            </div>
            {identity.revoked_at_unix_s ? (
              <div className="field-card">
                <span className="field-label">Revoked</span>
                {formatUnixSeconds(identity.revoked_at_unix_s)} -- {identity.revocation_reason || "no reason recorded"}
              </div>
            ) : null}
          </div>
        ) : null}

        {statusMessage ? <div className="success-banner">{statusMessage}</div> : null}

        {canMutate ? (
          <div className="operator-actions">
            <button
              className="button-secondary"
              type="button"
              disabled={identity.registration_status === "suspended"}
              onClick={() => setPendingAction({ kind: "lifecycle", action: "suspend" })}
            >
              Suspend
            </button>
            <button
              className="button-secondary"
              type="button"
              disabled={identity.registration_status === "active"}
              onClick={() => setPendingAction({ kind: "lifecycle", action: "activate" })}
            >
              Activate
            </button>
            <button
              className="button-primary danger-button"
              type="button"
              disabled={identity.registration_status === "revoked"}
              onClick={() => setPendingAction({ kind: "lifecycle", action: "revoke" })}
            >
              Revoke
            </button>
          </div>
        ) : (
          <div className="muted">Sign in as an admin to suspend, activate, or revoke this worker.</div>
        )}
      </article>

      <article className="card">
        <div className="eyebrow">Signing keys</div>
        <div className="audit-table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Key ID</th>
                <th>Status</th>
                <th>Activated</th>
                <th>Expires</th>
                <th>Source</th>
                {canMutate ? <th></th> : null}
              </tr>
            </thead>
            <tbody>
              {(signingKeys ?? []).map((key) => (
                <tr key={key.signing_key_id}>
                  <td>{key.signing_key_id}</td>
                  <td>
                    <SecurityStatusPill status={key.status} />
                  </td>
                  <td>{formatUnixSeconds(key.activated_at_unix_s)}</td>
                  <td>{formatUnixSeconds(key.expires_at_unix_s)}</td>
                  <td>{key.registration_source}</td>
                  {canMutate ? (
                    <td>
                      <button
                        className="button-secondary"
                        type="button"
                        disabled={key.status === "revoked"}
                        onClick={() => setPendingAction({ kind: "revoke-key", signingKeyId: key.signing_key_id })}
                      >
                        Revoke key
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
              {(signingKeys ?? []).length === 0 ? (
                <tr>
                  <td colSpan={canMutate ? 6 : 5} className="muted">
                    No signing keys visible for this role.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card">
        <div className="eyebrow">Recent activity</div>
        <div className="timeline-list compact-list">
          {recentEvents.length === 0 ? (
            <div className="muted">No recent security events reference this worker.</div>
          ) : (
            recentEvents.map((event) => (
              // event_id is only unique within its own source's
              // sequence, not across the merged Go-local/coordinator-
              // relayed response -- see security-events-console.tsx's
              // identical fix for the full explanation.
              <div className="timeline-item" key={`${event.source_service}-${event.event_id}`}>
                <div className="timeline-dot" />
                <div>
                  <strong>{event.event_type}</strong>
                  <div className="muted">
                    {formatTimestamp(event.timestamp)} · {event.outcome}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </article>

      {pendingCopy ? (
        <ConfirmDialog
          open={pendingAction !== null}
          title={pendingCopy.title}
          consequence={pendingCopy.consequence}
          confirmLabel={pendingCopy.confirmLabel}
          danger={pendingCopy.danger}
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
