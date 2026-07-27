# Coordinator Task Replay Protection (Worker-Side)

**Status: implemented, unit-tested, live-validated.**

## Design

`fl_platform.security.coordinator_task_replay.CoordinatorTaskReplayStore`
mirrors `cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp`'s
validate/commit transaction split (`validate()` is read-only;
`commit()` is only called after every other check — signature, hash
comparisons, expiry, worker binding — has already accepted the task),
but runs on the **worker**, tracking the **coordinator's issued
sequence** rather than a worker's own outgoing one.

Track key is `coordinator_signing_key_id` alone: a worker only ever
receives tasks from the one coordinator it is configured against, so
there is no separate `worker_id`/`message_stream` dimension to
disambiguate (unlike the coordinator-side `ReplayProtectionStore`,
which must handle many workers). Persistent, atomic
temp-file+`Path.replace()` writes — same convention as
`fl_platform.security.sequence_state.SequenceStateStore`.

Rejection reasons: `duplicate_or_lower_sequence` (the coordinator's
issued `sequence_number` for this signing key must be strictly
greater than the last one accepted), `duplicate_nonce` (even a
higher-sequence message is rejected if its nonce was already seen —
defends against a coordinator bug that reuses a nonce across distinct
sequence numbers).

Bounded: at most `kMaxNonceEntriesPerTrack` (256) recent nonces are
retained per track (oldest-first eviction) — matching
`ReplayProtectionStore`'s bound. Unlike that store, no time-based
nonce expiry is implemented: a worker's per-coordinator-key track
count is tiny (one coordinator, occasionally rotated keys), so the
fixed cap alone is a sufficient bound for this pass.

## Where it runs

`fl_platform.security.coordinator_task_verifier.verify_coordinator_task`
calls `replay_store.validate()` as the final check (after signature and
all five configuration hashes plus the payload hash), and
`replay_store.commit()` only once every prior check has passed — see
[signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s pipeline
description.

## Formal tests

`python/tests/test_coordinator_task_replay.py`: first-candidate
acceptance, lower/equal sequence rejection, duplicate-nonce rejection
even with a higher sequence, independent tracks per
`coordinator_signing_key_id`, restart persistence, nonce-cap eviction,
corruption detection.

## Live validation

A real Docker-built coordinator issued two live signed tasks in
sequence (an initial acquisition, then a lease-expiry-driven reissue)
to a real `GrpcCoordinatorClient`; the client's
`CoordinatorTaskReplayStore` accepted both (strictly increasing
sequence numbers, distinct nonces) and persisted its state to disk.
A `CoordinatorTaskReplayCandidate` reusing the first task's already-
committed sequence number was independently confirmed rejected. See
[signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s "Live
Docker validation" section for the full scenario list.

## What is deferred

* Time-based nonce expiry (relies on the fixed per-track cap only).
* Multi-coordinator support (a worker configured to fail over between
  multiple coordinator processes would need per-coordinator-identity
  tracks beyond just `coordinator_signing_key_id`, since two different
  coordinators could theoretically mint overlapping key_ids in a
  pathological setup) — not a concern for this deployment model
  (one worker, one coordinator).
