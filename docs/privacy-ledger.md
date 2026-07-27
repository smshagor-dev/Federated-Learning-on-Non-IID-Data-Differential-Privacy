# Privacy Ledger

**Status: implemented & tested, including a live Docker Compose
validation of the sample-level ledger's wire path.** Source:
`fl::coordinator::RunInstance` (`sample_level_ledger()`,
`user_level_ledger()`, `adaptive_clipping_ledger()` accessors,
`cpp/coordinator/include/fl_coordinator/run_manager.hpp`), the wire
messages `SampleLevelLedgerEntry`/`UserLevelLedgerEntry`/
`AdaptiveClippingLedgerEntry` (proto), Go's
`coordinator.PrivacyLedger` client-side mirror
(`go/internal/coordinator/*`), web's `PrivacyLedger` type
(`web/types/api.ts`). Tests: `cpp/coordinator/tests/user_level_dp_test.cpp`,
`cpp/coordinator/tests/adaptive_clipping_test.cpp`,
`cpp/coordinator/tests/coordinator_service_test.cpp`,
`python/tests/test_grpc_coordinator_client.py`.

## Three ledgers, one authority split

There is no single "privacy ledger" table. There are three, and each has
exactly one owner that computes its entries — every other component only
ever stores or relays what that owner sent:

| Ledger | Computed by | Stored/relayed by | Cardinality per round |
|---|---|---|---|
| `SampleLevelLedgerEntry` | Python worker (Opacus, per client) | C++ coordinator (`SubmitClientResult`), unmodified — optionally bound to an independently signed `SignedSamplePrivacyRecord` (see [signed-privacy-records.md](signed-privacy-records.md)) | 0..N (one per client that trained this round) |
| `UserLevelLedgerEntry` | C++ coordinator (`finalize_round`) | C++ coordinator | 0..1 (one iff user-level/hybrid DP active) |
| `AdaptiveClippingLedgerEntry` | C++ coordinator (`finalize_round`, after aggregation) | C++ coordinator | 0..1 (one iff adaptive clipping enabled) |

The coordinator is the sole source of truth for all three at rest — but
it only ever *computes* the latter two. For the sample-level ledger, the
coordinator's role is intentionally limited to storage and relay: it
does not recompute, re-derive, or validate the epsilon value a worker
reports, only that the entry's `run_id`/`round_id`/`client_id` match the
lease-validated submission it arrived with (see
[privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)
Section 3 for why that specific check exists and what it prevents).
**Updated**: when the submission carries an independently signed
`SignedSamplePrivacyRecord`, the coordinator additionally verifies the
record's own Ed25519 signature and enforces accountant-step/epsilon
monotonicity against `AccountantMonotonicityStore` before the entry is
appended — still never recomputing epsilon itself, only authenticating
and checking consistency of what the worker asserts. See
[signed-privacy-records.md](signed-privacy-records.md).

## Why they're never joined

A round with hybrid DP active produces one `UserLevelLedgerEntry` and as
many `SampleLevelLedgerEntry` records as clients participated — these
don't line up 1:1, so no code path attempts to zip them into per-round
rows. Doing so would visually invite exactly the kind of combined-epsilon
mistake the Critical Privacy Rule (see
[privacy-mathematics.md](privacy-mathematics.md)) forbids. The web
Privacy Center panel (`web/features/runs/privacy-center-panel.tsx`)
renders them as two separate tables for this reason, not as a stylistic
choice.

## Reading the ledger

`GetPrivacyLedger` (gRPC, C++) returns all three lists for a run,
optionally filtered by round range. Go's `/runs/{id}/privacy/ledger` HTTP
handler passes this through unmodified (no aggregation, no computed
summary fields beyond what the RPC already returns). The web panel polls
this endpoint alongside `/privacy/metrics` and `/privacy/projection` (see
[docker-runtime.md](docker-runtime.md) and known-limitations.md for the
polling-interval tradeoff).

## Checkpoint durability

All three ledgers are part of the coordinator's checkpoint body and
survive a restart — see [user-level-dp.md](user-level-dp.md)'s
"Checkpoint/recovery" section; the same mechanism covers all three
ledgers uniformly, not just the user-level one.

## Live validation

The sample-level ledger's full wire path (Python computes → gRPC
`SubmitClientResult` → C++ stores → gRPC `GetPrivacyLedger` → Go relays
→ observed via `curl`) was driven live through Docker Compose, and this
is exactly the path where the `entry_id`-dropping bug (see
[user-level-dp.md](user-level-dp.md)) was caught: a real UUID appeared in
Python's log line but an empty string appeared in the ledger entry
retrieved afterward, which would not have been visible from either
side's unit tests alone since each mocks the other side of the wire.
