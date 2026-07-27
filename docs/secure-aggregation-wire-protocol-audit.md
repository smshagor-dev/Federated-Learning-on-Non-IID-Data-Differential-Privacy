# Secure Aggregation Wire-Protocol Audit

**Status: audit only, performed before any wire-protocol code was
written, per the Required Working Method. Every row below reflects the
repository as it actually stands (confirmed by direct source
inspection and a dedicated research pass), not the target design.**
See [secure-aggregation-wire-protocol-foundation.md](secure-aggregation-wire-protocol-foundation.md)
for the scope decision this audit feeds into, and
[secure-aggregation-no-dropout-core-report.md](secure-aggregation-no-dropout-core-report.md)
for the cryptographic/math core this wire protocol will eventually sit
on top of.

## Method

Confirmed via direct `grep`/read of `proto/coordinator/coordinator.proto`,
`proto/worker/worker.proto`, `cpp/coordinator/src/coordinator_service.cpp`,
`cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp`,
`python/src/fl_platform/worker/coordinator_client.py`,
`python/src/fl_platform/worker/service.py`, `cpp/core/include/fl_core/aggregation.hpp`,
`cpp/coordinator/include/fl_coordinator/task_dispatcher.hpp`,
`cpp/coordinator/main.cpp`, `go/internal/coordinator/security_client.go`,
`go/internal/transport/httpapi/security_overview.go`,
`web/components/security-subnav.tsx`, `infra/compose/docker-compose*.yml`,
`scripts/security-validation/registry.py`, `.github/workflows/ci.yml` —
not assumed from prior documentation.

## Headline finding

**Zero wire-protocol surface exists for secure aggregation.** No
`SecureAggregation*` proto message or RPC appears anywhere in `proto/`.
No `MESSAGE_TYPE_SECURE_AGG_*`/`MESSAGE_STREAM_SECURE_AGGREGATION` wire
enum value exists in `worker.proto` (only a C++-only,
protobuf-independent `MessageStream::kSecureAggregation` placeholder
exists in `replay_protection_store.hpp`, added in the prior slice, with
its own comment stating no RPC constructs a candidate on it yet). No
`SecureAggregationSessionManager` or equivalent orchestration class
exists in C++ or Python — only the protobuf-free, gRPC-free pure-math
library (`secure_aggregation_{encoding,mask,session,crypto,tensor_mask}`)
from the prior slice. This is a from-scratch wire-protocol
implementation, not an extension of partially-built RPCs.

## Protocol-surface table

| Operation | Contract exists | C++ impl | Python impl | Auth requirement | Signature requirement | Replay stream | Session-state requirement | Worker-status requirement | Persistence requirement | Current validation | Remaining work |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Create secure session | No | No | N/A (coordinator-only) | N/A (internal, triggered by round start) | N/A | N/A | None → `COHORT_FORMING` | All selected participants ACTIVE | Safe session metadata only | None | Full: proto config message, `RunManager`/`RoundManager` hook, `SecureAggregationSessionManager::create_session` |
| Read secure session (`GetSecureAggregationSession`) | No | No | N/A | mTLS + ADMIN_CONTROL-style identity (read) | None | N/A | Any | N/A | N/A (read-only) | None | Full RPC + response message |
| Advertise ephemeral key (`AdvertiseSecureAggregationKey`) | No | No | No | mTLS + worker cert identity | Ed25519, worker signing key | New: `SECURE_AGGREGATION` (schema placeholder only) | `COHORT_FORMING`/`KEY_ADVERTISEMENT` | ACTIVE, valid signing key | Advertisement record (safe fields) | None | Full: proto messages, C++ handler (27-step verification per Work Package G), Python client call |
| Read frozen cohort roster (`GetFrozenCohortRoster`) | No | No | No | mTLS + worker cert identity, must be roster member | Coordinator Ed25519 signature over roster | N/A (read) | `COHORT_FROZEN` or later | ACTIVE, in roster | None new (roster derived from session state) | None | Full: proto message, coordinator signing (reuses `coordinator_signing_identity.cpp` pattern), C++ handler, Python verification (`coordinator_task_verifier.py`-style) |
| Submit masked update (`SubmitMaskedClientUpdate`) | No | No | No | mTLS + worker cert identity | Ed25519, worker signing key | New: `SECURE_AGGREGATION` (same stream, key-advertisement-then-masked-update ordering) | `MASKED_UPDATE_COLLECTION` | ACTIVE, in roster | Masked contribution (safe metadata + temporary masked bytes) | None | Full: proto messages (largest new message set — masked tensors, checksums, encoding stats), C++ handler (37-step verification per Work Package O), Python client call, tensor→ring bridge |
| Abort secure session (`AbortSecureAggregationSession`) | No | No | N/A (coordinator/admin-triggered; workers observe via roster/deadline, not a direct RPC) | mTLS + ADMIN_CONTROL identity (manual) or internal (deadline/dropout) | None (manual); N/A (automatic) | N/A | Any non-terminal → `ABORTED` | N/A | Cleanup of temporary masked data | `CohortStateMachine::abort` exists and is tested (pure state transition only — no session registry, no RPC, no cleanup side effects wired) | RPC + real session registry + real temporary-storage cleanup |
| Finalize secure session (internal, triggered by complete cohort) | No | No | N/A | N/A (internal) | N/A | N/A | `MASKED_UPDATE_COLLECTION` → `AGGREGATE_VALIDATION` → `COMPLETED` | N/A | Aggregate checksum (safe) | `sum_masked_tensors`/`decode_value` exist and are tested (pure functions only — no bridge to `fl::core::TensorCollection`/`ClientUpdate`/`AggregatorRegistry`) | Bridge code + FedAvg integration + model-version advance |
| List secure sessions (`ListSecureAggregationSessions`) | No | No | N/A | mTLS + read identity | None | N/A | Any | N/A | N/A (read-only) | None | Full RPC, pagination/filter (mirrors `ListSecurityEvents`'s cursor pattern) |
| Read secure session status (subset of the above, or a dedicated summary RPC) | No | No | N/A | mTLS + read identity | None | N/A | Any | N/A | N/A | None | Folded into `GetSecureAggregationSession`/`ListSecureAggregationSessions` above rather than a ninth RPC — no functional gap distinct from those two |

## Supporting infrastructure this protocol must plug into (confirmed real and reusable)

- **`ReplayProtectionStore`**: real, tested, `validate()`-before/
  `commit()`-after-domain-success two-step already used by
  `Heartbeat`/`SubmitClientResult` — the exact pattern a new secure-
  aggregation RPC handler must replicate on the (already-reserved)
  `MessageStream::kSecureAggregation` track.
- **`coordinator_signing_identity.cpp`/`coordinator_task_signing.cpp`**:
  real Ed25519 signing/verification machinery the frozen-roster
  signature (Work Package J) and secure-task binding (Work Package F)
  will reuse rather than duplicate.
- **`TaskDispatcher::cancel_lease_for_worker`**: a real, already-live
  "forcibly end this worker's active work" primitive (used today by
  `SuspendWorker`/`RevokeWorker`) that Work Package S's participant-
  status-change aborts can call into directly.
- **`CoordinatorServiceImpl`'s constructor injection pattern**: 13
  existing optional dependencies (stores/registries/journals) wired
  the same way in every constructor call site (`main.cpp`) — a
  `SecureAggregationSessionManager*` fits this exact, already-
  established pattern.
- **`AggregatorRegistry`/`Aggregator::aggregate`**: a real, pure,
  already-tested FedAvg (and other algorithm) entry point over
  `std::vector<ClientUpdate>` — confirmed unchanged from the prior
  audit, still whole-tensor-in-memory (no chunked/streaming path). A
  decoded aggregate from secure aggregation can be handed to this
  exact function once a `MaskedClientUpdate → TensorCollection` bridge
  exists; no rewrite of this aggregation core is needed or planned.

## Confirmed real gaps (not assumed)

1. Docker Compose worker topology is single-instance, hand-pinned (one
   `python-worker` service, hardcoded `worker-1` certs) — not
   parameterized for a multi-worker cohort. Real work required before
   any 3+-worker Docker validation (Work Package AG) is possible.
2. Go's `secure_aggregation_available` flag in the security overview
   endpoint is already an honest, explicit `false` — not a stub to
   silently flip, a deliberate disclosure to keep accurate until real.
3. `security-validation` harness has a clean, minimal registration
   point (`scripts/security-validation/registry.py`) — adding a new
   `secure-aggregation-no-dropout` group is mechanically simple once
   there is a live protocol to write real (non-`SKIPPED`) scenarios
   against.
4. CI's `cpp-grpc` job already builds/tests the gRPC-gated coordinator
   target set for real — a new secure-aggregation gRPC test target
   would slot into its existing target list with no new CI
   infrastructure required.

See [secure-aggregation-wire-protocol-foundation.md](secure-aggregation-wire-protocol-foundation.md)
for how this audit's findings shape this pass's Tier 1/Tier 2 scope
decision.
