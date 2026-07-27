# Message Streams and Sequence Numbers

**Status: Implemented and Validated (live) for `HEARTBEAT` (including a
real restart), `CLIENT_RESULT`, `PRIVACY_RECORD`, and
`KEY_MANAGEMENT`.** Every stream enum value is defined; these four have
real producers (the live-test scripts, and — for
`CLIENT_RESULT`/`PRIVACY_RECORD`/`KEY_MANAGEMENT` — the actual
production `GrpcCoordinatorClient.submit_result`/`rotate_signing_key`
code paths) and consumers (`coordinator_service.cpp`'s
`Heartbeat`/`SubmitClientResult`/`RotateWorkerSigningKey` handlers).

## The seven streams

```text
MESSAGE_STREAM_CONTROL, MESSAGE_STREAM_HEARTBEAT,
MESSAGE_STREAM_TASK_LIFECYCLE, MESSAGE_STREAM_CLIENT_RESULT,
MESSAGE_STREAM_PRIVACY_RECORD, MESSAGE_STREAM_PERSONALIZATION,
MESSAGE_STREAM_KEY_MANAGEMENT
```

Defined as a nested enum on `fl.worker.v1.SignedWorkerEnvelope`
(`proto/worker/worker.proto`) and mirrored as a plain C++ enum,
`fl::coordinator::MessageStream`, in the protobuf-free
`replay_protection_store.hpp` (see that header's comment on why it's a
separate type rather than reusing the protobuf enum directly — this
store has no protobuf dependency at all, matching
`WorkerIdentityRegistry`'s equally protobuf-free, locally-buildable
design).

## Track scoping

Sequence state is tracked per `(worker_id, signing_key_id, message_stream)`
triple, not just per worker — confirmed by both unit test and live
Docker test: registering a second signing key for the same worker (a
hypothetical today, since key rotation isn't implemented — see
[key-rotation.md](key-rotation.md)) or using a different stream for the
same worker/key both start a fully independent sequence track at 1,
never interfering with an existing track's state. This is what makes
future key rotation possible without needing to somehow "transfer"
sequence history between keys — a `GRACE_PERIOD` key (once
implemented) would simply keep incrementing its own already-established
track.

## Rules (all implemented and tested for the general mechanism, exercised live for `HEARTBEAT`)

* **Starting value**: the documented starting value for a brand-new
  track is **1**, not 0 — enforced, not just conventional (see
  [replay-protection.md](replay-protection.md)'s gap-policy note: even
  a track's first-ever message is subject to the `max_sequence_gap`
  check against an implicit `last_sequence_number = 0`).
* **Duplicate sequences are rejected** (`kDuplicateSequence`).
* **Lower sequences are rejected** (`kLowerSequence`).
* **Excessive gaps follow a configured policy**: reject-only,
  `max_sequence_gap` (default 1000), applied uniformly. No permissive/
  accept-with-warning policy is implemented.
* **Key rotation sequence behavior**: not implemented (no rotation
  exists), but the track-scoping design above means a rotated-to key
  would naturally start its own sequence at 1 rather than continuing
  the old key's count — this is a *consequence* of the scoping decision
  already made, not a separately implemented rule.
* **Grace-period keys**: not implemented (no grace periods exist), but
  would, by the same track-scoping logic, maintain fully independent
  sequence state from the key they're superseding.
* **Sequence rollover**: not specially handled. `sequence_number` is
  `uint64` — a wraparound would require roughly 1.8×10¹⁹ messages from
  a single `(worker_id, signing_key_id, message_stream)` track, judged
  out of scope for this pass. A wraparound would manifest as a rejected
  lower-sequence message (a safe, if inconvenient, failure mode), never
  a bypass.
* **Sequence state survives restart**: validated live — see
  [replay-protection.md](replay-protection.md)'s "Validated live"
  section.

## Deterministic tests

`run_replay_protection_store_tests` (`cpp/coordinator/tests/replay_protection_store_test.cpp`)
exercises every rule above against explicit, hand-constructed sequence
values (never randomized), including the cross-track independence case
(different key, different stream, different worker) and the restart-
persistence case (closing and reopening the store mid-test). All pass
locally via MSVC.

## What is deferred

* `TASK_LIFECYCLE` and `PERSONALIZATION` still have no real producer or
  consumer — see [signed-worker-envelopes.md](signed-worker-envelopes.md)'s
  "What is deferred" section.
* No metric or event exists for sequence violations yet (a rejection is
  visible only via the gRPC error and `WorkerHeartbeatResponse.rejection_code`
  today).
