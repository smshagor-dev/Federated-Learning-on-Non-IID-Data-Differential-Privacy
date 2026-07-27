"use client";

import { useEffect, useState, type ReactNode } from "react";

// Web Security Center slice: every admin mutation (worker suspend/
// activate/revoke, worker signing-key revoke, coordinator signing-key
// rotate/revoke) routes through this one dialog rather than each page
// hand-rolling its own confirm flow, so the required reason +
// confirmation + consequence explanation + idempotency key + safe-retry
// behavior stays consistent across every action instead of drifting
// per-page.

export type ConfirmDialogInput = {
  reason: string;
  idempotencyKey: string;
};

export function ConfirmDialog({
  open,
  title,
  consequence,
  confirmLabel,
  danger = false,
  busy = false,
  error,
  onCancel,
  onConfirm,
  children,
}: {
  open: boolean;
  title: string;
  consequence: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  error?: string | null;
  onCancel: () => void;
  onConfirm: (input: ConfirmDialogInput) => void | Promise<void>;
  // Optional action-specific fields (e.g. coordinator signing-key
  // rotation's expected-current-key/expiry/grace-period inputs) rendered
  // between the consequence text and the reason field. State for these
  // fields is owned by the caller, not this dialog -- ConfirmDialog only
  // ever hands back { reason, idempotencyKey } from onConfirm, so a
  // caller using children reads its own field state at confirm time.
  children?: ReactNode;
}) {
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }
    // Minted once per dialog open, then reused across every confirm
    // click for this open session -- if a mutation fails and the
    // operator retries without closing the dialog, the retry carries the
    // same key so the server-side idempotency cache (see
    // go/internal/transport/httpapi/security_handlers.go's
    // idempotencyCache) treats it as the same request instead of
    // executing the action twice.
    setIdempotencyKey(crypto.randomUUID());
    setReason("");
    setAcknowledged(false);
  }, [open]);

  if (!open) {
    return null;
  }

  const canConfirm = reason.trim().length > 0 && acknowledged && !busy;

  return (
    <div className="modal-overlay" role="presentation" onClick={busy ? undefined : onCancel}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="card-title" id="confirm-dialog-title">
          {title}
        </h2>
        <p className="card-copy">{consequence}</p>
        {children}
        <label className="field-card">
          <span className="field-label">Reason (required)</span>
          <textarea
            className="textarea"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Explain why this action is being taken -- recorded in the security audit journal."
            disabled={busy}
          />
        </label>
        <label className="modal-ack-row">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            disabled={busy}
          />
          <span>I have read the consequences above and confirm this action.</span>
        </label>
        <div className="muted modal-idempotency-note">Idempotency key for this attempt: {idempotencyKey}</div>
        {error ? <div className="notice">{error}</div> : null}
        <div className="operator-actions">
          <button className="button-secondary" type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={danger ? "button-primary danger-button" : "button-primary"}
            type="button"
            disabled={!canConfirm}
            onClick={() => onConfirm({ reason: reason.trim(), idempotencyKey })}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
