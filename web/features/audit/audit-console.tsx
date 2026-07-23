"use client";

import { useEffect, useMemo, useState, useTransition } from "react";

import { listAuditEventsWithToken } from "@/lib/api";
import type { AuditEvent, AuthSession } from "@/types/api";

export function AuditConsole({ seedEvents }: { seedEvents: AuditEvent[] }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>(seedEvents);
  const [query, setQuery] = useState("");
  const [resourceFilter, setResourceFilter] = useState("all");
  const [outcomeFilter, setOutcomeFilter] = useState("all");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    const cached = window.localStorage.getItem("fl-platform-session");
    if (!cached) {
      return;
    }
    try {
      const parsed = JSON.parse(cached) as AuthSession;
      setSession(parsed);
      void refreshEvents(parsed.token);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  async function refreshEvents(token: string) {
    try {
      const liveEvents = await listAuditEventsWithToken(token, 120);
      setEvents(liveEvents);
      setError(null);
    } catch (fetchError) {
      const message = fetchError instanceof Error ? fetchError.message : "Unable to load audit events";
      setError(message);
    }
  }

  function handleRefresh() {
    if (!session?.token) {
      setError("Sign in as a researcher or admin to inspect the protected audit feed.");
      return;
    }
    startTransition(async () => {
      await refreshEvents(session.token);
    });
  }

  const filteredEvents = useMemo(() => {
    return events.filter((event) => {
      const matchesQuery =
        query.trim() === "" ||
        `${event.action} ${event.actor_email ?? ""} ${event.resource_type} ${event.resource_id ?? ""}`
          .toLowerCase()
          .includes(query.toLowerCase());
      const matchesResource = resourceFilter === "all" || event.resource_type === resourceFilter;
      const matchesOutcome = outcomeFilter === "all" || event.outcome === outcomeFilter;
      return matchesQuery && matchesResource && matchesOutcome;
    });
  }, [events, outcomeFilter, query, resourceFilter]);

  return (
    <div className="content-stack">
      <article className="card audit-hero-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Governance console</div>
            <h2 className="card-title">Searchable audit workspace</h2>
            <p className="card-copy">
              Inspect auth, project, experiment, and run history from one screen. Researchers and admins can refresh
              the protected feed live when a session token is available.
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">User: {session?.user.display_name ?? "Guest viewer"}</span>
            <span className="pill">Role: {session?.user.role ?? "guest"}</span>
            <span className="pill">Events loaded: {events.length}</span>
          </div>
        </div>
        <div className="section-grid">
          <label className="field-card">
            <span className="field-label">Search</span>
            <input
              className="input"
              placeholder="Search action, actor, resource, or ID"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label className="field-card">
            <span className="field-label">Resource Type</span>
            <select className="select" value={resourceFilter} onChange={(event) => setResourceFilter(event.target.value)}>
              <option value="all">all</option>
              <option value="session">session</option>
              <option value="project">project</option>
              <option value="experiment">experiment</option>
              <option value="run">run</option>
            </select>
          </label>
          <label className="field-card">
            <span className="field-label">Outcome</span>
            <select className="select" value={outcomeFilter} onChange={(event) => setOutcomeFilter(event.target.value)}>
              <option value="all">all</option>
              <option value="success">success</option>
              <option value="denied">denied</option>
            </select>
          </label>
          <div className="field-card">
            <span className="field-label">Quick Action</span>
            <button className="button-primary" disabled={isPending} onClick={handleRefresh} type="button">
              {isPending ? "Refreshing..." : "Refresh audit feed"}
            </button>
          </div>
        </div>
        {error ? <div className="notice">{error}</div> : null}
      </article>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Filtered Events</h2>
          <div className="audit-table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Actor</th>
                  <th>Resource</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {filteredEvents.map((event) => (
                  <tr key={event.id}>
                    <td>{event.action}</td>
                    <td>{event.actor_email ?? "system"}</td>
                    <td>
                      {event.resource_type} {event.resource_id ?? "n/a"}
                    </td>
                    <td>{event.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
        <article className="card">
          <h2 className="card-title">Event Detail Rail</h2>
          <div className="timeline-list audit-timeline">
            {filteredEvents.map((event) => (
              <div className="timeline-item" key={event.id}>
                <div className="timeline-dot" />
                <div>
                  <strong>{event.action}</strong>
                  <div className="muted">
                    {event.actor_email ?? "system"} • {event.actor_role ?? "n/a"} • {event.timestamp}
                  </div>
                  <div className="pill-row" style={{ marginTop: 8 }}>
                    <span className="pill">{event.resource_type}</span>
                    <span className="pill">{event.outcome}</span>
                    {event.resource_id ? <span className="pill">{event.resource_id}</span> : null}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </article>
      </div>
    </div>
  );
}
