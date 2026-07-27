"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { SecuritySubNav } from "@/components/security-subnav";
import { SecurityStatusPill } from "@/components/security-status-pill";
import { listSecurityEventsWithToken } from "@/lib/security-api";
import { formatTimestamp } from "@/lib/security-format";
import { useStoredSession } from "@/lib/use-stored-session";
import type { SecurityEventRecord } from "@/types/api";

const POLL_INTERVAL_MS = 5_000;
const PAGE_LIMIT = 100;
// Bounded ring buffer: Work Package "no unbounded browser-side event
// buffer" -- a live-polled feed that never drops old entries would grow
// without limit over a long-lived tab. Oldest events are dropped once
// this cap is exceeded.
const MAX_BUFFERED_EVENTS = 500;

export function SecurityEventsConsole() {
  const session = useStoredSession();
  const token = session?.token;
  const [events, setEvents] = useState<SecurityEventRecord[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [minSeverity, setMinSeverity] = useState("all");
  const [subjectType, setSubjectType] = useState("all");
  const [eventType, setEventType] = useState("");
  const [query, setQuery] = useState("");

  const lastEventIdRef = useRef<string>("");

  useEffect(() => {
    if (!token) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    lastEventIdRef.current = "";
    setEvents([]);

    async function poll(isRefetch: boolean) {
      const result = await listSecurityEventsWithToken(
        token as string,
        {
          limit: PAGE_LIMIT,
          afterEventId: isRefetch ? undefined : lastEventIdRef.current || undefined,
          minSeverity: minSeverity === "all" ? undefined : minSeverity,
          subjectType: subjectType === "all" ? undefined : subjectType,
          eventType: eventType.trim() || undefined,
        },
        controller.signal,
      );
      if (cancelled) {
        return;
      }
      setUnavailable(result === undefined);
      if (result === undefined || result.length === 0) {
        return;
      }
      lastEventIdRef.current = result[result.length - 1]?.event_id ?? lastEventIdRef.current;
      setEvents((current) => {
        const merged = isRefetch ? result : [...current, ...result];
        return merged.length > MAX_BUFFERED_EVENTS ? merged.slice(merged.length - MAX_BUFFERED_EVENTS) : merged;
      });
    }

    void poll(true);
    const interval = setInterval(() => void poll(false), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [eventType, minSeverity, subjectType, token]);

  const filteredEvents = useMemo(() => {
    const matches = query.trim() === "" ? events : events.filter((event) =>
      `${event.event_type} ${event.safe_actor_id} ${event.safe_subject_id} ${event.worker_id}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
    );
    return [...matches].reverse();
  }, [events, query]);

  if (!token) {
    return (
      <div className="content-stack">
        <SecuritySubNav current="/security/events" />
        <article className="card security-hero-card">
          <div className="eyebrow">Security event explorer</div>
          <p className="card-copy">Sign in to view security events.</p>
        </article>
      </div>
    );
  }

  return (
    <div className="content-stack">
      <SecuritySubNav current="/security/events" />
      <article className="card security-hero-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Security event explorer</div>
            <h2 className="card-title">Signed-message, privacy-record, and task security events</h2>
            <p className="card-copy">
              Live-polled feed (every {POLL_INTERVAL_MS / 1000}s) merged from the Go-local and coordinator-relayed
              event journals, capped at the {MAX_BUFFERED_EVENTS} most recent events shown here.
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">Buffered: {events.length}</span>
          </div>
        </div>
        <div className="section-grid">
          <label className="field-card">
            <span className="field-label">Search</span>
            <input className="input" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <label className="field-card">
            <span className="field-label">Minimum severity</span>
            <select className="select" value={minSeverity} onChange={(event) => setMinSeverity(event.target.value)}>
              <option value="all">all</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="HIGH">HIGH</option>
              <option value="CRITICAL">CRITICAL</option>
            </select>
          </label>
          <label className="field-card">
            <span className="field-label">Subject type</span>
            <select className="select" value={subjectType} onChange={(event) => setSubjectType(event.target.value)}>
              <option value="all">all</option>
              <option value="worker">worker</option>
              <option value="coordinator">coordinator</option>
              <option value="task">task</option>
              <option value="signing_key">signing_key</option>
            </select>
          </label>
          <label className="field-card">
            <span className="field-label">Event type (exact)</span>
            <input className="input" value={eventType} onChange={(event) => setEventType(event.target.value)} />
          </label>
        </div>
        {unavailable ? <div className="notice">Coordinator/journal is not reachable right now.</div> : null}
      </article>

      <article className="card">
        <div className="audit-table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Severity</th>
                <th>Event type</th>
                <th>Source</th>
                <th>Subject</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.map((event) => (
                // Real bug found by this slice's live Playwright browser
                // suite: GET /api/v1/security/events merges two
                // independently-sequenced journals (Go-local and
                // coordinator-relayed -- see security_handlers.go's
                // handleSecurityEvents), each assigning its own
                // event_id starting from 1, so event_id is NOT
                // globally unique across the merged response -- two
                // unrelated events from different sources can share
                // the same event_id. Keying solely on event_id made
                // React reuse a stale DOM row for a different event
                // after a filter/re-sort, rendering the wrong event's
                // content. source_service + event_id is unique because
                // each source owns one independent sequence.
                <tr key={`${event.source_service}-${event.event_id}`}>
                  <td>{formatTimestamp(event.timestamp)}</td>
                  <td>
                    <SecurityStatusPill status={event.severity} />
                  </td>
                  <td>{event.event_type}</td>
                  <td>{event.source_service}</td>
                  <td>
                    {event.subject_type}: {event.safe_subject_id}
                  </td>
                  <td>
                    <SecurityStatusPill status={event.outcome} />
                  </td>
                </tr>
              ))}
              {filteredEvents.length === 0 ? (
                <tr>
                  <td colSpan={6} className="muted">
                    No events match the current filters.
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
