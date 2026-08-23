# Execution Backend Event Observability

The unified execution control plane keeps lifecycle state and backend event history as two separate durable concerns.

## Event source

Distributed executions use the C++ coordinator's resumable `StreamRunEvents` feed through the Go coordinator client's `PollEvents` abstraction. The execution backend implements the optional `execution.EventSource` capability, so local backends that do not expose a resumable stream keep the base lifecycle `Driver` contract unchanged.

No tensor payloads are copied into the Go control plane. Only coordinator event identifiers, type, round, reason, trace identifier, timestamp, and scalar/string metadata are ingested.

## Durable cursor

Each execution record stores `backend_event_cursor`, the last backend event identifier whose batch was processed. The cursor is persisted in the same durable execution repository as lifecycle state.

Backend events are written to `execution-events.jsonl` before the cursor advances. Journal insertion uses a stable execution-scoped identifier:

`<execution-id>-backend-<backend-event-id>`

`Journal.AppendUnique` refuses to append the same identifier twice. This ordering makes restart replay safe:

1. append the backend event to the durable journal;
2. persist the backend resume cursor;
3. if the process stops between those operations, the backend may replay the same event;
4. the unique journal append becomes a no-op and the cursor can advance without duplicating operator-visible history.

## Event shape

Backend event types are exposed in the execution journal as `COORDINATOR_<TYPE>`. Coordinator fixed fields that do not have a first-class execution-event field are preserved in metadata. Examples include:

- `client_id`
- `worker_id`
- `model_version`
- `failure_kind`
- `backend_timestamp`
- `backend_event_id`
- `backend_event_type`
- `source=coordinator`

A distributed client that misses the absolute communication-round deadline is therefore visible through the existing execution events endpoint as a coordinator task-failure event with `failure_kind=round_timeout` and its client/worker identifiers when available.

## Reconciliation policy

Startup reconciliation performs an event catch-up for active distributed executions. Periodic runtime reconciliation polls backend events only after the backend snapshot changed. This is intentional because the coordinator event RPC is a bounded long poll; completely idle executions should not consume that long-poll window on every reconciliation tick.

Deadline completion or deadline failure changes run status, round, or model version, so the runtime reconciliation path immediately performs event ingestion for those state changes.

Terminal records that have never persisted an event cursor receive one startup recovery poll. This covers a process restart after the backend completed but before the control plane had copied its final event history.

## HTTP surface

No new browser-facing transport is required. Ingested backend events are returned by the existing endpoint:

`GET /api/v1/executions/{execution-id}/events?limit=<n>`

The endpoint remains a fast read of the durable journal; it does not open a coordinator stream per request.

## Boundary

This event ingestion layer reports what the backend emits; it does not infer or recompute federated-learning state. Round deadlines, timeout classification, retry exhaustion, aggregation, and privacy accounting remain authoritative in the C++ coordinator. The Go control plane only persists and exposes their event evidence.
