"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import { listSecurityAuditWithToken } from "@/lib/security-api";
import { formatTimestamp } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { SecurityAuditRecord } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;
const PAGE_LIMIT = 200;
// Same bounded-buffer rationale as the event explorer -- see
// security-events-console.tsx.
const MAX_BUFFERED_RECORDS = 500;

export function SecurityAuditConsole() {
  const session = useStoredSession();
  const token = session?.token;
  const [records, setRecords] = useState<SecurityAuditRecord[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [query, setQuery] = useState("");

  const cursorRef = useRef<string>("");

  useEffect(() => {
    if (!token) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    cursorRef.current = "";
    setRecords([]);

    async function poll(isRefetch: boolean) {
      const result = await listSecurityAuditWithToken(
        token as string,
        {
          limit: PAGE_LIMIT,
          cursor: isRefetch ? undefined : cursorRef.current || undefined,
          actor: actor.trim() || undefined,
          action: action.trim() || undefined,
          resourceType: resourceType.trim() || undefined,
          outcome: outcome === "all" ? undefined : outcome,
        },
        controller.signal,
      );
      if (cancelled) {
        return;
      }
      setUnavailable(result === undefined);
      if (result === undefined) {
        return;
      }
      if (result.next_cursor) {
        cursorRef.current = result.next_cursor;
      }
      if (result.records.length === 0) {
        return;
      }
      setRecords((current) => {
        const merged = isRefetch ? result.records : [...current, ...result.records];
        return merged.length > MAX_BUFFERED_RECORDS ? merged.slice(merged.length - MAX_BUFFERED_RECORDS) : merged;
      });
    }

    void poll(true);
    const interval = setInterval(() => void poll(false), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [action, actor, outcome, resourceType, token]);

  const filteredRecords = useMemo(() => {
    const matches =
      query.trim() === ""
        ? records
        : records.filter((record) =>
            `${record.action} ${record.safe_actor_id ?? ""} ${record.resource_type} ${record.resource_id ?? ""}`
              .toLowerCase()
              .includes(query.trim().toLowerCase()),
          );
    return [...matches].reverse();
  }, [query, records]);

  if (!token) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/audit" />
        <article className="card security-hero-card">
          <div className="eyebrow">Security audit explorer</div>
          <p className="card-copy">Sign in to view the durable security audit journal.</p>
        </article>
      </div>
    );
  }

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security/audit" />
      <article className="card security-hero-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Security audit explorer</div>
            <h2 className="card-title">Durable, append-only security audit trail</h2>
            <p className="card-copy">
              Every security admin action (worker suspend/activate/revoke, signing-key revoke, coordinator key
              rotate/revoke) recorded here, independent of the general-purpose audit feed at /audit.
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">Buffered: {records.length}</span>
          </div>
        </div>
        <div className="section-grid">
          <label className="field-card">
            <span className="field-label">Search</span>
            <input className="input" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <label className="field-card">
            <span className="field-label">Actor</span>
            <input className="input" value={actor} onChange={(event) => setActor(event.target.value)} />
          </label>
          <label className="field-card">
            <span className="field-label">Action</span>
            <input className="input" value={action} onChange={(event) => setAction(event.target.value)} />
          </label>
          <label className="field-card">
            <span className="field-label">Resource type</span>
            <input className="input" value={resourceType} onChange={(event) => setResourceType(event.target.value)} />
          </label>
          <label className="field-card">
            <span className="field-label">Outcome</span>
            <select className="select" value={outcome} onChange={(event) => setOutcome(event.target.value)}>
              <option value="all">all</option>
              <option value="success">success</option>
              <option value="denied">denied</option>
              <option value="failed">failed</option>
            </select>
          </label>
        </div>
        {unavailable ? <div className="notice">Audit journal is not reachable right now.</div> : null}
      </article>

      <article className="card">
        <div className="audit-table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Resource</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {filteredRecords.map((record) => (
                <tr key={record.record_id}>
                  <td>{formatTimestamp(record.timestamp)}</td>
                  <td>{record.safe_actor_id ?? "n/a"}</td>
                  <td>{record.action}</td>
                  <td>
                    {record.resource_type} {record.resource_id ?? "n/a"}
                  </td>
                  <td>
                    <SecurityStatusPill status={record.outcome} />
                  </td>
                </tr>
              ))}
              {filteredRecords.length === 0 ? (
                <tr>
                  <td colSpan={5} className="muted">
                    No audit records match the current filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>
    </div>
  );
}
