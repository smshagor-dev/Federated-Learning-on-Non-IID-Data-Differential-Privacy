"use client";

import { useEffect, useState } from "react";

import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import { getSecurityEventSourcesWithToken, getSecurityOverviewWithToken } from "@/lib/security-api";
import { formatBoolean, formatLagSeconds, formatTimestamp, formatUnixSeconds } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { AuthRole, SecurityEventSourcesResponse, SecurityOverview } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;

// Route-visibility table (docs/security-permission-model.md): the
// overview is readable by ADMIN/RESEARCHER/VIEWER; SERVICE has no
// PermOverviewRead grant. This mirrors that on the client so a
// service-role session sees an explicit "not available for this role"
// message instead of a generic "coordinator unreachable" notice -- the
// real enforcement is still the Go 403 (go/internal/security/permissions.go),
// this is display-only, per the plan's "do not rely only on hidden UI
// controls" decision.
const OVERVIEW_ALLOWED_ROLES: AuthRole[] = ["admin", "researcher", "viewer"];

export function SecurityOverviewConsole() {
  const session = useStoredSession();
  const token = session?.token;
  const role = session?.user.role;
  const [overview, setOverview] = useState<SecurityOverview | undefined>(undefined);
  const [sources, setSources] = useState<SecurityEventSourcesResponse | undefined>(undefined);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!token || (role && !OVERVIEW_ALLOWED_ROLES.includes(role))) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    async function poll() {
      const [overviewResult, sourcesResult] = await Promise.all([
        getSecurityOverviewWithToken(token as string, controller.signal),
        getSecurityEventSourcesWithToken(token as string, controller.signal),
      ]);
      if (cancelled) {
        return;
      }
      setUnavailable(overviewResult === undefined);
      setOverview(overviewResult);
      setSources(sourcesResult);
    }

    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [role, token]);

  if (!token) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security" />
        <article className="card security-hero-card">
          <div className="eyebrow">Security Center</div>
          <p className="card-copy">Sign in as an admin, researcher, or viewer to inspect security posture.</p>
        </article>
      </div>
    );
  }

  if (role && !OVERVIEW_ALLOWED_ROLES.includes(role)) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security" />
        <article className="card security-hero-card">
          <div className="eyebrow">Security Center</div>
          <div className="notice">
            The service role does not have overview read access. Sign in as an admin, researcher, or viewer instead.
          </div>
        </article>
      </div>
    );
  }

  if (unavailable || !overview) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security" />
        <article className="card security-hero-card">
          <div className="eyebrow">Security Center</div>
          <div className="notice">
            {unavailable
              ? "Coordinator is not reachable right now, so security posture cannot be loaded."
              : "Loading security overview..."}
          </div>
        </article>
      </div>
    );
  }

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security" />

      <article className="card security-hero-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Security Center</div>
            <h2 className="card-title">Aggregate security posture</h2>
            <p className="card-copy">
              Message authenticity and observability status across transport, worker/coordinator signing keys, and
              the durable event/audit journals. Generated {formatUnixSeconds(overview.generated_at_unix_s)}.
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">Role: {role}</span>
            <span className="pill">Recent critical events: {overview.recent_critical_event_count}</span>
            <span className="pill">Recent high-severity events: {overview.recent_high_severity_event_count}</span>
          </div>
        </div>
        <div className="security-limitation-banner">
          <strong>Scope disclosure:</strong> this platform provides mutual TLS transport, Ed25519 message signing,
          and durable security event/audit journals for observability. It does not implement secure aggregation,
          worker attestation, verifiable client clipping, or Byzantine-robust aggregation --
          {" "}
          {overview.feature_availability.central_coordinator_observes_updates
            ? "the central coordinator can observe individual worker updates in plaintext."
            : "individual worker update visibility is unknown."}
        </div>
      </article>

      <div className="triple-grid">
        <article className="card">
          <div className="eyebrow">Transport</div>
          <h3 className="card-title">{overview.transport.transport_mode || "unknown"}</h3>
          <div className="pill-row">
            <SecurityStatusPill status={overview.transport.coordinator_available ? "connected" : "unavailable"} />
            <span className="pill">mTLS enforced: {formatBoolean(overview.transport.mutual_tls_enforced)}</span>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            Checked {formatUnixSeconds(overview.transport.checked_at_unix_s)}
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Worker identities</div>
          <div className="pill-row">
            <span className="pill">Active: {overview.worker_identities.active}</span>
            <span className="pill">Suspended: {overview.worker_identities.suspended}</span>
            <span className="pill">Revoked: {overview.worker_identities.revoked}</span>
            <span className="pill">Expired: {overview.worker_identities.expired}</span>
          </div>
          {overview.worker_identities.certificate_expiry_warnings > 0 ? (
            <div className="notice" style={{ marginTop: 10 }}>
              {overview.worker_identities.certificate_expiry_warnings} certificate(s) expiring within 7 days.
            </div>
          ) : null}
        </article>

        <article className="card">
          <div className="eyebrow">Worker signing keys</div>
          <div className="pill-row">
            <span className="pill">Active: {overview.worker_signing_keys.active}</span>
            <span className="pill">Grace period: {overview.worker_signing_keys.grace_period}</span>
            <span className="pill">Expired: {overview.worker_signing_keys.expired}</span>
            <span className="pill">Revoked: {overview.worker_signing_keys.revoked}</span>
          </div>
          {overview.worker_signing_keys.recent_rotation_failures > 0 ? (
            <div className="notice" style={{ marginTop: 10 }}>
              {overview.worker_signing_keys.recent_rotation_failures} recent rotation failure(s).
            </div>
          ) : null}
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <div className="eyebrow">Coordinator signing keys</div>
          <div className="pill-row">
            {overview.coordinator_keys.active_key_id ? (
              <span className="pill">Active key: {overview.coordinator_keys.active_key_id}</span>
            ) : (
              <span className="pill">Active key: hidden for this role</span>
            )}
            <span className="pill">Bundle version: {overview.coordinator_keys.trusted_bundle_version}</span>
            <SecurityStatusPill status={overview.coordinator_keys.bundle_healthy ? "active" : "warning"} />
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            Active key expires {formatUnixSeconds(overview.coordinator_keys.active_key_expires_at_unix_s)}
            {overview.coordinator_keys.grace_period_key_id
              ? ` · grace-period key ends ${formatUnixSeconds(overview.coordinator_keys.grace_period_end_unix_s)}`
              : ""}
          </div>
          <div className="muted">
            Historical: {overview.coordinator_keys.historical_expired_count} expired,{" "}
            {overview.coordinator_keys.historical_revoked_count} revoked
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Signed messages</div>
          <div className="pill-row">
            <span className="pill">Accepted: {overview.signed_messages.accepted}</span>
            <span className="pill">Rejected: {overview.signed_messages.rejected}</span>
            <span className="pill">Signature failures: {overview.signed_messages.signature_failures}</span>
            <span className="pill">Payload-hash failures: {overview.signed_messages.payload_hash_failures}</span>
            <span className="pill">Replay rejections: {overview.signed_messages.replay_rejections}</span>
            <span className="pill">Sequence rejections: {overview.signed_messages.sequence_rejections}</span>
          </div>
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <div className="eyebrow">Privacy record authenticity</div>
          <div className="pill-row">
            <span className="pill">Accepted: {overview.privacy_records.accepted}</span>
            <span className="pill">Rejected: {overview.privacy_records.rejected}</span>
            <span className="pill">Monotonicity violations: {overview.privacy_records.monotonicity_violations}</span>
            <span className="pill">Config mismatches: {overview.privacy_records.configuration_mismatches}</span>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            This tracks whether the coordinator accepted each worker&apos;s self-reported epsilon/delta as
            authentic and monotonically increasing -- it is not an independent privacy audit of the accounting
            math itself.
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Coordinator tasks</div>
          <div className="pill-row">
            <span className="pill">Signed: {overview.tasks.signed}</span>
            <span className="pill">Signing failures: {overview.tasks.signing_failures}</span>
            <span className="pill">Verification failures: {overview.tasks.verification_failures}</span>
            <span className="pill">Replay rejections: {overview.tasks.replay_rejections}</span>
            <span className="pill">Duplicate execution blocks: {overview.tasks.duplicate_execution_blocks}</span>
            <span className="pill">Reissues: {overview.tasks.reissues}</span>
          </div>
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <div className="eyebrow">Security event journal</div>
          <div className="pill-row">
            <span className="pill">Records: {overview.event_journal.size_records}</span>
            <SecurityStatusPill status={overview.event_journal.corrupted ? "corrupted" : "active"} />
            <span className="pill">Rotation active: {formatBoolean(overview.event_journal.retention_active)}</span>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            Last record {formatTimestamp(overview.event_journal.last_record_at)} (
            {formatLagSeconds(overview.event_journal.lag_seconds)}) · {overview.event_journal.recovered_lines}{" "}
            recovered line(s)
          </div>
        </article>

        <article className="card">
          <div className="eyebrow">Security audit journal</div>
          <div className="pill-row">
            <span className="pill">Records: {overview.audit_journal.size_records}</span>
            <SecurityStatusPill status={overview.audit_journal.corrupted ? "corrupted" : "active"} />
            <span className="pill">Rotation active: {formatBoolean(overview.audit_journal.retention_active)}</span>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            Last record {formatTimestamp(overview.audit_journal.last_record_at)} (
            {formatLagSeconds(overview.audit_journal.lag_seconds)}) · {overview.audit_journal.recovered_lines}{" "}
            recovered line(s)
          </div>
        </article>
      </div>

      {sources ? (
        <article className="card">
          <div className="eyebrow">Event source health</div>
          <div className="audit-table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Last event</th>
                  <th>Lag</th>
                  <th>Records</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {sources.sources.map((source) => (
                  <tr key={source.source_service}>
                    <td>{source.source_service}</td>
                    <td>{formatTimestamp(source.last_event_at)}</td>
                    <td>{formatLagSeconds(source.lag_seconds)}</td>
                    <td>{source.record_count}</td>
                    <td>
                      <SecurityStatusPill
                        status={source.corrupted ? "corrupted" : source.stale ? "stale" : "active"}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}
    </div>
  );
}
