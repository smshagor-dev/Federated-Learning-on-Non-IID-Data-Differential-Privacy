# Security Event Centralization

**Status: Implemented and validated (C++, Python).** Web Security
Center, Event Centralization, and Security CI slice, Work Package L.
See [security-events.md](security-events.md) for the shared event
schema this slice relays, and
[known-limitations.md](known-limitations.md) for the disclosed gaps
this slice does and does not close.

## What this closes

Before this slice, Python-worker-originated security events were
persisted only to the worker's own local JSONL journal
(`security_event_journal.py`) and exposed via Prometheus on that
process alone — never visible to an operator looking at the
coordinator's or Go's security surfaces. This slice adds a real,
signed relay path: a worker batches its locally-queued events and
submits them to the coordinator over the exact same
`SignedWorkerEnvelope` pipeline that already authenticates
`Heartbeat`/`SubmitClientResult`/`RotateWorkerSigningKey`, and the
coordinator journals accepted events into its own
`SecurityEventJournal` tagged `source_service="python-worker"` — no
second, parallel event store anywhere in this pipeline.

## Wire contract

New proto messages (`proto/worker/worker.proto`,
`proto/coordinator/coordinator.proto`):

- `SignedWorkerEnvelope.MessageType.MESSAGE_TYPE_SECURITY_EVENT_BATCH = 12`
- `SignedWorkerEnvelope.MessageStream.MESSAGE_STREAM_SECURITY_EVENTS = 8`
  (its own independent sequence-number track — a burst of queued
  events must never be rejected as a "sequence gap" against an
  unrelated stream's counter)
- `WorkerSecurityEventPayload`: one queued event, worker-supplied.
  Deliberately does **not** include `event_id`, `source_service`, or
  `payload_checksum` — those are coordinator-assigned on acceptance,
  mirroring `SecurityEvent`'s existing "assigned by the journal, not
  the caller" convention.
- `SignedWorkerSecurityEventBatch`: `schema_version`, `worker_id`,
  `events` (repeated `WorkerSecurityEventPayload`, in submission
  order — **not** re-sorted, unlike `SubmitClientResult`'s tensor/
  metric lists, because event order is itself meaningful and part of
  what gets signed), `queue_depth_hint` (worker's own self-reported
  remaining-queue estimate — explicitly untrusted, see below).
- `SubmitWorkerSecurityEventsRequest` / `Response`: the RPC envelope.
  `Response` carries `accepted` (whole-batch), `rejection_code`
  (whole-batch, stable machine-readable), `accepted_event_count`,
  `rejected_event_count` (individual events skipped within an accepted
  batch), `last_accepted_event_id` (coordinator-assigned, for
  operator-facing display only — **not** used by the worker's own
  cursor logic, see below).

Canonicalization (`security_event_batch_payload_hash_input`, C++
`signed_envelope_verifier.cpp` / Python `signed_envelope.py`): every
field in alphabetical key order, `json.dumps(...,sort_keys=True,
separators=(",", ":"))`-equivalent, matching every other
`*_payload_hash_input` function's convention exactly. Verified
byte-for-byte identical between languages via a cross-language golden
fixture embedded in both `signed_envelope_verifier_test.cpp`'s
`kGoldenBatchJson` and `test_security_event_batch.py`'s
`test_golden_hash_matches_the_cross_language_fixture`.

## Coordinator-side verification (`SubmitWorkerSecurityEvents` RPC, `coordinator_service.cpp`)

Same pipeline as `RotateWorkerSigningKey`, in order:

1. mTLS worker-identity binding (`reject_if_worker_identity_mismatch`).
2. Worker must exist and not be `SUSPENDED`/`REVOKED`/`EXPIRED`.
3. `batch.worker_id` must match the request's `worker_id`.
4. Batch size bound: `events_size() > kMaxSecurityEventBatchSize` (200)
   is rejected wholesale (`batch_too_large`) — never silently
   truncated.
5. Signing key resolved via the shared `resolve_signing_key`/
   `SignedMessageKind::kSecurityEventBatch` (permits `ACTIVE` or
   `GRACE_PERIOD`, same as heartbeat/client-result/privacy-record — a
   worker mid-rotation must still be able to flush its queue).
6. Envelope signature + payload-hash verification
   (`verify_signed_envelope`).
7. Replay protection on `MessageStream::kSecurityEvents` (its own
   independent sequence/nonce track).
8. **Only after all of the above pass**, each event in the batch is
   individually validated (recognized `event_type`/`severity`/
   `actor_type`/`subject_type`/`outcome` strings, plus the shared
   `validate_security_event` bounds) and journaled one at a time — an
   unrecognized or malformed individual event is skipped
   (`rejected_event_count` increments) but does **not** fail the
   already-authenticated batch.
9. The coordinator also emits one `WORKER_SECURITY_EVENT_BATCH_ACCEPTED`
   (or `_REJECTED`, for whole-batch rejections) event about the batch
   itself, `source_service="coordinator"` — distinct from the
   individual relayed events, which keep `source_service="python-worker"`.

## Worker-side queue (`python/src/fl_platform/worker/security_event_queue.py`)

`WorkerSecurityEventQueue` wraps an existing `SecurityEventJournal`
instance as its storage engine — the same journal
`_emit_security_event` already writes to, not a second store. It adds
only what the journal doesn't provide:

- `select_pending(max_batch_size)`: everything after the last
  acknowledged cursor, via the journal's own `after_event_id`
  cursor filter.
- `mark_acknowledged(event_id)`: persisted in a small sidecar
  `<journal>.cursor` JSON file, atomic temp-file-then-replace write
  (same convention as `signing_key_rotation.py`'s
  `save_rotation_state`).
- `pending_count_hint()`: feeds the batch's `queue_depth_hint` field.

**Delivery semantics: at-least-once, never silent loss.** The cursor
only advances after `GrpcCoordinatorClient.submit_security_events`
receives `response.accepted == True` — a crash or a rejected
submission between `select_pending()` and `mark_acknowledged()` means
the same events are selected again on the next attempt. This is a
deliberate choice: an occasional harmless re-submission (deduped by
the coordinator's replay-protection store if the envelope is retried
verbatim, or simply re-journaled as new records if a fresh envelope is
signed) is preferred over ever losing a security event.

**Known limitation** (see `security_event_queue.py`'s own docstring
and [known-limitations.md](known-limitations.md)): the journal only
serves its currently-active, not-yet-rotated file
(`SecurityEventJournal.list`'s existing, pre-existing scope). If a
worker falls far enough behind that rotation happens before
`select_pending()` has picked up and acknowledged everything, the
un-acknowledged records in the rotated-away file become unreachable to
this queue (though they remain on disk in the rotated file for
out-of-band inspection). At the default 10 MiB/5-generation rotation
policy this requires a very large local backlog.

## Untrusted signals

`queue_depth_hint` (worker-self-reported) and, more generally,
anything an individual worker asserts about its own local state, is
never treated as ground truth by `GetSecurityEventSourceHealth` — see
that RPC's own proto comment. Centrally-observable aggregates only:
batch accept/reject counts, distinct-worker-IDs-seen, and the
coordinator's own journal health are what `GetSecurityEventSourceHealth`
reports for the `"python-worker"` source.

## Metrics

`fl_security_event_source_records`, `fl_security_event_source_batches`
(`outcome="accepted"|"rejected"`), `fl_security_event_source_distinct_workers`,
and `fl_security_event_source_lag_seconds` — all Prometheus gauges in
Go (`go/internal/observability/telemetry.go`), fed on every
`GET /api/v1/security/events/sources` request from exactly the
response that endpoint is about to serve (one source of truth, no
separate re-derivation). `source_service` is one of a small fixed set
(`"go-api"`, `"coordinator"`, `"python-worker"`) — low-cardinality by
construction. An unknown lag (no record observed yet) is never coerced
to `0`; it is simply omitted from that gauge's series.

## What this does not do

- Does not implement secure aggregation, pairwise masking, or any of
  the other explicitly-out-of-scope items listed in
  [known-limitations.md](known-limitations.md)'s "Explicitly out of
  scope" section — this is a message-authenticity and observability
  relay for security *events*, not a change to how training updates
  themselves are aggregated or protected.
- Does not add a new C++ Prometheus `/metrics` endpoint — Go re-exports
  the coordinator's own already-computed aggregate via the existing
  telemetry pattern instead (see "Metrics" above).
- Does not exercise this RPC over a live Docker Compose stack with a
  real Python worker process in this pass — see
  [known-limitations.md](known-limitations.md) for what validation
  *was* performed (a live Docker gRPC `ctest` build exercising
  `CoordinatorServiceImpl` directly, plus isolated Python unit tests of
  the queue/signing logic).
