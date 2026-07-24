"use client";

import { useEffect, useRef, useState } from "react";

import { getCoordinatorHealth, getCoordinatorRun, type CoordinatorAvailability } from "@/lib/api";
import { subscribeToCoordinatorEvents, type CoordinatorStreamStatus } from "@/lib/coordinator-events";
import type { CoordinatorEvent, CoordinatorRunSnapshot } from "@/types/api";

const MAX_EVENT_HISTORY = 25;
const HEALTH_POLL_INTERVAL_MS = 5_000;

// This panel is additive: it surfaces the live C++ coordinator's round
// state and event stream (Milestone 3) alongside the existing
// project/experiment/run bookkeeping dashboard (Milestone 1), which
// keeps working unchanged whether or not a coordinator is configured.
// See docs/go-coordinator-integration.md.
export function CoordinatorStatusPanel({ runId, token }: { runId: string; token: string | undefined }) {
  const [availability, setAvailability] = useState<CoordinatorAvailability | "idle">("idle");
  const [snapshot, setSnapshot] = useState<CoordinatorRunSnapshot | undefined>(undefined);
  const [streamStatus, setStreamStatus] = useState<CoordinatorStreamStatus>("connecting");
  const [events, setEvents] = useState<CoordinatorEvent[]>([]);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!token) {
      setAvailability("idle");
      return;
    }

    let cancelled = false;
    async function pollHealthAndRun() {
      const healthResult = await getCoordinatorHealth(token as string);
      if (cancelled) return;
      setAvailability(healthResult.availability);
      if (healthResult.availability === "connected") {
        const run = await getCoordinatorRun(runId, token as string);
        if (!cancelled) {
          setSnapshot(run);
        }
      }
    }

    void pollHealthAndRun();
    const interval = setInterval(() => void pollHealthAndRun(), HEALTH_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId, token]);

  useEffect(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    if (!token || availability !== "connected") {
      return;
    }
    unsubscribeRef.current = subscribeToCoordinatorEvents(runId, token, {
      onStatusChange: setStreamStatus,
      onEvent: (event) => {
        setEvents((previous) => [event, ...previous].slice(0, MAX_EVENT_HISTORY));
      },
    });
    return () => {
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
    };
  }, [runId, token, availability]);

  if (!token) {
    return (
      <article className="card">
        <div className="eyebrow">Coordinator status</div>
        <p className="card-copy">Sign in to view the live federated coordinator's round state and event stream.</p>
      </article>
    );
  }

  return (
    <article className="card">
      <div className="eyebrow">Coordinator status</div>
      <h3 className="card-title">Federated round state</h3>
      <div className="pill-row">
        <span className="pill">{describeAvailability(availability)}</span>
        {availability === "connected" ? <span className="pill">Stream: {streamStatus}</span> : null}
        {snapshot ? <span className="pill">State: {snapshot.state}</span> : null}
      </div>
      {availability === "unavailable" ? (
        <div className="notice">
          Coordinator is not reachable right now (no coordinator process, or FL_COORDINATOR_ADDRESS is unset). Local
          run bookkeeping above is unaffected.
        </div>
      ) : null}
      {availability === "unauthorized" ? (
        <div className="notice">Session does not have permission to view coordinator status.</div>
      ) : null}
      {snapshot ? (
        <div className="pill-row">
          <span className="pill">
            Round {snapshot.current_round} / {snapshot.max_rounds || "?"}
          </span>
          <span className="pill">Model: {snapshot.model_version}</span>
          <span className="pill">Algorithm: {snapshot.algorithm}</span>
          <span className="pill">
            Workers: {snapshot.healthy_workers}/{snapshot.registered_workers}
          </span>
        </div>
      ) : null}
      {events.length > 0 ? (
        <ul className="event-feed">
          {events.map((event) => (
            <li key={event.event_id}>
              <span className="pill">{event.type}</span> round {event.round_id}
              {event.client_id ? ` · client ${event.client_id}` : ""}
              {event.worker_id ? ` · worker ${event.worker_id}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

function describeAvailability(availability: CoordinatorAvailability | "idle"): string {
  switch (availability) {
    case "connected":
      return "Coordinator: connected";
    case "unavailable":
      return "Coordinator: unavailable";
    case "unauthorized":
      return "Coordinator: unauthorized";
    case "unknown":
      return "Coordinator: unknown";
    case "idle":
      return "Coordinator: checking...";
  }
}
