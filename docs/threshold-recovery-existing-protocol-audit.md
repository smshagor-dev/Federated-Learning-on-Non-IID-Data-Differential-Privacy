# Threshold Recovery Existing Protocol Audit

Access date: July 28, 2026.

## Scope

This audit records what the repository's current secure-aggregation path
actually implements before any threshold-recovery dependency is selected.
Evidence is limited to the current code, tests, and live validation reruns.

## Evidence Summary

| Area | Current state | Evidence level | Evidence |
|---|---|---|---|
| Session creation | One session per `(run_id, round_id)` is created on first `AcquireTask` for a secure round. | Direct code inspection | `cpp/coordinator/src/coordinator_service.cpp`, `SecureAggregationSessionManager::create_session` |
| Provider | Only `SECAGG_NO_DROPOUT_EXPERIMENTAL` is assigned on the wire. | Direct code inspection | `cpp/coordinator/src/coordinator_service.cpp` |
| Key advertisement | Workers generate a fresh ephemeral X25519 keypair per session, sign the advertisement, and the coordinator verifies identity, signature, replay state, and session binding before acceptance. | Direct code inspection + live validation | `python/src/fl_platform/worker/service.py`, `python/src/fl_platform/secure_aggregation/key_advertisement.py`, `cpp/coordinator/src/coordinator_service.cpp`, `scripts/validate_secure_cohort_handshake.py` |
| Frozen roster | The roster is auto-frozen only after every ordered participant advertises. The coordinator signs the roster when a signing identity is configured. | Direct code inspection | `SecureAggregationSessionManager::freeze_cohort` |
| Masked update path | Secure tasks use a masked-only submission path. There is no cleartext fallback after a secure handshake succeeds. | Direct code inspection | `python/src/fl_platform/worker/service.py` |
| Replay separation | Key advertisements and masked updates use distinct replay/sequence streams. | Direct code inspection | `cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp`, `cpp/coordinator/src/coordinator_service.cpp` |
| Persistence | Persistent session storage contains only session metadata, timestamps, and terminal reasons. It does not persist ephemeral private keys, pairwise secrets, masks, or shares. | Direct code inspection | `cpp/coordinator/include/fl_coordinator/secure_aggregation_session_store.hpp` |
| Finalization rule | Finalization refuses any partial cohort and explicitly requires abort-on-dropout instead of partial aggregation. | Direct code inspection | `cpp/coordinator/src/secure_aggregation_session_manager.cpp` |
| Adaptive clipping indicator | Only the complete-cohort aggregate indicator count is decoded; no individual indicator is exposed. | Direct code inspection | `SecureAggregationSessionManager::decode_secure_adaptive_clipping_indicator_count`, coordinator finalization path |
| Restart behavior | Persisted non-terminal session records are reconciled to aborted-on-restart metadata, but there is no live share recovery state to resume. | Direct code inspection | `SecureAggregationSessionStore::reconcile_after_restart` |

## Current State Machine Boundaries

The current implementation supports:

- cohort formation
- signed key advertisement
- complete-cohort roster freeze
- masked update collection
- complete-cohort finalization
- secure user-level DP under secure aggregation
- secure hybrid DP under secure aggregation
- secure adaptive clipping with privately aggregated binary indicators

The current implementation does not support:

- threshold share generation
- encrypted share fan-out
- recovery-share collection
- survivor-set recomputation after dropouts
- partial-cohort unmasking
- threshold reconstruction of dropped-user material
- secure finalization after any participant dropout

## Concrete No-Dropout Enforcement Points

The repository is intentionally no-dropout today:

1. `AcquireTask` creates secure sessions with `minimum_cohort_size == cohort_size`.
2. `freeze_cohort` requires every configured participant to advertise.
3. `submit_masked_update` accepts only frozen-roster members and records one contribution per worker.
4. `finalize` rejects any incomplete cohort and says the caller must abort for dropout.
5. The worker keeps the ephemeral private key only in session-scoped memory and discards it after masked submission or task failure.

## Validation Rerun Notes

The current baseline was revalidated before this evaluation:

- `python scripts/check_project_terminology.py` passed.
- `python scripts/verify_proto_contracts.py` passed.
- `build\\cpp-debug\\Debug\\fl_coordinator_tests.exe` passed.
- `python scripts/validate_secure_cohort_handshake.py` passed: `7/7`.
- `python scripts/validate_masked_update_runtime.py` passed on rerun: `15/15`.

One earlier Docker compose bootstrap failure appeared transient and did not reproduce on rerun. No protocol regression was observed.

## Audit Conclusion

The repository already has a real, validated secure-aggregation runtime,
but it is a complete-cohort protocol. Threshold recovery is not partially
implemented; it is structurally absent by design.
