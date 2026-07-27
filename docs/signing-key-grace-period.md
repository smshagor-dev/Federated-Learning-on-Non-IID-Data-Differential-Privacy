# Signing-Key Grace-Period Behavior

**Status: Implemented and Validated live**, including real acceptance
during the grace window and real rejection after genuine elapsed time.
See `SigningKeyRegistry::commit_rotation`/`effective_record`
(`cpp/coordinator/src/signing_key_registry.cpp`).

## During grace period

* The new key is `ACTIVE` and preferred.
* The previous key is `GRACE_PERIOD`, with `grace_period_start_unix_s`/
  `grace_period_end_unix_s` set at commit time
  (`now + requested_grace_period_seconds`).
* Both keys may verify Heartbeat/client-result/privacy-record messages
  (see [signing-key-management.md](signing-key-management.md)'s
  enforcement table) -- capability refresh and a new rotation request
  still require the `ACTIVE` key specifically.
* Sequence and replay state remain fully independent per key
  (`ReplayProtectionStore`'s track key already includes
  `signing_key_id`) -- no changes were needed there.

## After grace-period expiry

* The old key becomes `EXPIRED`.
* New messages signed by it are rejected --
  `signing_key_status_permits` returns `false` for `EXPIRED` against
  every message kind.
* Historical records it previously signed remain exactly as verifiable
  as they always were (nothing about a past, already-accepted
  signature changes retroactively) -- expiry only affects *new*
  verification attempts going forward.
* Its replay/sequence state and public-key metadata both remain
  retained in their respective stores -- neither
  `ReplayProtectionStore::purge_worker` nor any equivalent for
  `SigningKeyRegistry` is called on ordinary expiry (only full worker
  revocation purges replay state).

## Status transitions are evaluated at verification time, not only by a background sweep

Per the specification's explicit requirement ("do not rely exclusively
on a long-running background timer; status must also be evaluated at
verification time"): `SigningKeyRegistry::find`/`find_active`/
`has_any_valid_key`/`list_for_worker` all compute a key's *effective*
status relative to the `now_unix_s` passed in on every call
(`effective_record`), independent of whether `sweep_expired()` has ever
run. `sweep_expired()` exists purely to **persist** an already-computed
lazy transition for administration-surface consistency
(`list_for_worker` after a restart, for instance) -- it is not what
makes expiry actually enforced.

## Live validation

See [signing-key-management.md](signing-key-management.md)'s "Live
validation" section, scenarios 4-6: a message signed with the still-
valid `GRACE_PERIOD` key was accepted; after a real 5-second wait past
a 5-second requested grace period, the identical key was rejected, with
the rejection message naming its status as `expired` -- proving the
lazy, verification-time evaluation actually fired (no maintenance
sweep was ever triggered in this test run).

## What is deferred

* No periodic background sweep is actually scheduled anywhere in this
  pass (`sweep_expired()` exists and is unit-tested, but nothing calls
  it automatically on a timer) -- consistent with this codebase's
  existing convention elsewhere (e.g. `WorkerRegistry::sweep_unhealthy`'s
  identical caller-driven design) but worth stating plainly, since nothing
  currently drives that caller for signing keys either.
* No configurable per-worker or per-run grace-period default -- only a
  single global maximum (`kMaxGracePeriodSeconds`, 24 hours); the
  actual requested value is fully caller-controlled per rotation
  request, up to that ceiling.
