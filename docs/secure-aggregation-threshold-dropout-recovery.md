# Threshold Dropout Recovery for Pairwise Secure Aggregation

## Status

This document defines the v3 dropout-recovery math now implemented in
`fl_platform.secure_aggregation.dropout_recovery`. It does **not** claim that the
C++ coordinator gRPC service transports recovery shares yet.

## Why the recoverable secret is the ephemeral X25519 private key

The current secure-aggregation runtime masks each participant update with one
pairwise stream per peer. A stream is derived from:

1. the participant's session-scoped X25519 private key,
2. the peer's frozen-roster X25519 public key,
3. the existing HKDF purpose label, and
4. the existing canonical mask context bound to provider, protocol version,
   session, run, round, model version, cohort commitment, ordered participant
   pair, tensor name, and chunk index.

If participant `D` drops after the cohort is frozen but before submitting its
masked update, every survivor still has a pairwise mask involving `D` in its
submitted update. The missing side that would have been contributed by `D` is
therefore the exact opposite-signed mask stream derived from `D`'s ephemeral
private key and each survivor's public key.

Threshold-sharing an arbitrary unrelated seed is insufficient for this protocol.
The threshold secret must be `D`'s exact 32-byte ephemeral X25519 private key.

## Recovery sequence

1. A worker creates its ephemeral X25519 keypair for one secure-aggregation
   session.
2. Before masked-update collection, it Shamir-shares the **32-byte ephemeral
   private key** among the designated holder workers with the configured
   threshold. Shares remain session/owner/generation/holder bound by the existing
   threshold-recovery primitive.
3. The public key is advertised and becomes part of the signed frozen roster.
4. If the worker submits normally, its recovery shares are never needed and the
   coordinator never learns the private key.
5. If the worker is declared dropped, at least the threshold number of surviving
   holders resubmit their shares.
6. The reconstructed 32-byte secret is converted back to an X25519 public key and
   must exactly match the dropped participant's public key in the frozen roster.
   A mismatch aborts recovery.
7. For each surviving peer, the coordinator derives the same X25519 shared secret
   and reuses the existing canonical tensor, weight, and clipping-indicator mask
   derivation functions.
8. The coordinator computes the mask sign from the **dropped participant's**
   perspective and adds those missing mask contributions to the aggregate of
   surviving masked updates.
9. The resulting ring values equal the aggregate of the surviving clear encoded
   updates, weights, and clipping indicators; the dropped participant's model
   update is not invented or included.

## Privacy boundary

Recovery exposes only the ephemeral private key of a participant already
classified as dropped. It does not reveal any survivor private key. From the
dropped key plus survivor public keys the coordinator can reproduce pairwise masks
involving that dropped participant, which is exactly the information needed to
remove leftover masks. It still cannot derive pairwise masks solely between two
surviving participants without one of their private keys.

Raw Shamir shares and recovered private keys remain secret protocol material and
must not be written to the durable secure-aggregation session store. Existing
commitment-only recovery receipts remain the persistence boundary.

## Current validation

`python/tests/test_secure_aggregation_dropout_recovery.py` constructs a real
three-participant X25519 cohort using the same production crypto/mask functions as
the masked-update runtime. Two workers submit masked tensor, weight, and clipping
indicator values while the third drops. A threshold of shares reconstructs the
dropped worker's ephemeral key, verifies it against the advertised public key,
recreates its missing pairwise mask side, and recovers the exact two-survivor ring
sums.

The tests also reject insufficient shares, a reconstructed key that does not
match the frozen roster, a wrong private key, and correction tensor-shape drift.

## Remaining live-protocol work

Before v3.0.0 can advertise threshold dropout recovery as supported, the following
must still be implemented and validated end to end:

- signed share-distribution/receipt semantics among holder workers,
- signed holder-to-coordinator recovery-share submission,
- coordinator-side threshold reconstruction in the live C++ service,
- explicit dropout declaration and recovery state transitions,
- recovery deadlines and replay/idempotency rules,
- C++ reproduction/application of the missing pairwise mask streams,
- durable commitment-only restart reconciliation,
- multi-process dropout/restart integration tests, and
- interaction validation with differential privacy and adaptive clipping.

Until those items are green, the capability matrix must continue to reject
threshold recovery as release-supported.
