"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import { listSecurityWorkersWithToken } from "@/lib/security-api";
import { formatUnixSeconds } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { SecurityWorker } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;
const PAGE_SIZE = 25;

// Work Package D: Worker Security List. The Go endpoint
// (GET /api/v1/security/workers) returns the full identity set, not a
// cursor page -- there is no server-side pagination to call here, so
// pagination/filter/sort are all client-side, matching this codebase's
// existing convention (features/audit/audit-console.tsx does the same
// over its full event list) rather than inventing a cursor protocol the
// backend doesn't implement.
export function SecurityWorkersConsole() {
  const session = useStoredSession();
  const token = session?.token;
  const [workers, setWorkers] = useState<SecurityWorker[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState<"worker_id" | "registration_status" | "expires_at_unix_s">("worker_id");
  const [page, setPage] = useState(0);

  useEffect(() => {
    if (!token) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    async function poll() {
      const result = await listSecurityWorkersWithToken(token as string, controller.signal);
      if (cancelled) {
        return;
      }
      setUnavailable(result === undefined);
      if (result !== undefined) {
        setWorkers(result);
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

  const filtered = useMemo(() => {
    const list = workers.filter((worker) => {
      const matchesQuery = query.trim() === "" || worker.worker_id.toLowerCase().includes(query.trim().toLowerCase());
      const matchesStatus = statusFilter === "all" || worker.registration_status === statusFilter;
      return matchesQuery && matchesStatus;
    });
    return [...list].sort((a, b) => {
      if (sortKey === "expires_at_unix_s") {
        return (a.expires_at_unix_s ?? 0) - (b.expires_at_unix_s ?? 0);
      }
      return String(a[sortKey] ?? "").localeCompare(String(b[sortKey] ?? ""));
    });
  }, [query, sortKey, statusFilter, workers]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  const showDetailColumns = workers.length === 0 || workers[0].certificate_fingerprint !== undefined;

  if (!token) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/workers" />
        <article className="card security-hero-card">
          <div className="eyebrow">Worker identities</div>
          <p className="card-copy">Sign in to view registered worker identities.</p>
        </article>
      </div>
    );
  }

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security/workers" />
      <article className="card security-hero-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Worker identities</div>
            <h2 className="card-title">Registered worker certificates and signing keys</h2>
            <p className="card-copy">
              {showDetailColumns
                ? "Certificate identity, signing-key binding, and lifecycle timestamps for every registered worker."
                : "Aggregate-only view for this role -- certificate fingerprints and signing-key identifiers are not shown."}
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">Workers loaded: {workers.length}</span>
          </div>
        </div>
        <div className="section-grid">
          <label className="field-card">
            <span className="field-label">Search worker ID</span>
            <input className="input" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <label className="field-card">
            <span className="field-label">Status</span>
            <select className="select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">all</option>
              <option value="active">active</option>
              <option value="suspended">suspended</option>
              <option value="revoked">revoked</option>
              <option value="expired">expired</option>
            </select>
          </label>
          <label className="field-card">
            <span className="field-label">Sort by</span>
            <select
              className="select"
              value={sortKey}
              onChange={(event) => setSortKey(event.target.value as typeof sortKey)}
            >
              <option value="worker_id">worker ID</option>
              <option value="registration_status">status</option>
              {showDetailColumns ? <option value="expires_at_unix_s">certificate expiry</option> : null}
            </select>
          </label>
        </div>
        {unavailable ? <div className="notice">Coordinator is not reachable right now.</div> : null}
      </article>

      <article className="card">
        <div className="audit-table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Worker ID</th>
                <th>Status</th>
                {showDetailColumns ? <th>Signing key</th> : null}
                {showDetailColumns ? <th>Certificate expires</th> : null}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((worker) => (
                <tr key={worker.worker_id}>
                  <td>{worker.worker_id}</td>
                  <td>
                    <SecurityStatusPill status={worker.registration_status} />
                  </td>
                  {showDetailColumns ? <td>{worker.signing_key_id ?? "n/a"}</td> : null}
                  {showDetailColumns ? <td>{formatUnixSeconds(worker.expires_at_unix_s)}</td> : null}
                  <td>
                    <Link className="inline-link" href={`/security/workers/${worker.worker_id}`}>
                      View
                    </Link>
                  </td>
                </tr>
              ))}
              {pageItems.length === 0 ? (
                <tr>
                  <td colSpan={showDetailColumns ? 5 : 3} className="muted">
                    No workers match the current filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="operator-actions" style={{ marginTop: 12 }}>
          <button
            className="button-secondary"
            type="button"
            disabled={page === 0}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            Previous
          </button>
          <span className="muted">
            Page {page + 1} of {pageCount}
          </span>
          <button
            className="button-secondary"
            type="button"
            disabled={page >= pageCount - 1}
            onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
          >
            Next
          </button>
        </div>
      </article>
    </div>
  );
}
