# Secure Aggregation Live Threshold Recovery

## Status

This v3 slice connects the previously validated Shamir/X25519 recovery
primitives to the live coordinator transport. It is an experimental recovery
path, not a claim that every secure-aggregation configuration is now dropout
resilient.

## Implemented path

1. A surviving holder submits `SecureAggregationRecoveryShare` over the
   recovery gRPC service.
2. The request is bound to the holder's authenticated worker identity and
   Ed25519 signing key through `SignedWorkerEnvelope`.
3. The coordinator validates the frozen session/run/round/model/cohort
   commitment and requires the holder to already have submitted its masked
   contribution.
4. Recovery shares use their own replay/sequence track, independent of key
   advertisement and masked-update submission.
5. Raw Shamir share values live only in the recovery service's in-memory
   collector. Status responses expose commitment receipts only.
6. When the threshold is reached, the reconstructed 32-byte X25519 private key
   is checked against the missing participant's public key from the signed
   frozen roster.
7. The coordinator constructs a zero-data, zero-weight synthetic contribution
   containing only the missing participant's pairwise mask side.
8. That correction is admitted through the existing
   `SecureAggregationSessionManager::submit_masked_update` validation path.
9. The existing complete-cohort `finalize()` implementation decodes the
   corrected aggregate, and the existing run bridge advances the model.

The coordinator never fabricates a dropped client's clear update. The synthetic
contribution represents zero data and exists only to cancel the unmatched
pairwise masks left by the surviving contributors.

## Initial supported policy

The live recovery adapter is intentionally fail-closed outside this scope:

- exactly one frozen-cohort participant is missing;
- the recovery holder must be a surviving participant whose masked update was
  already accepted;
- the recovered secret is exactly one 32-byte ephemeral X25519 private key;
- the run algorithm is FedAvg;
- privacy mode is `NONE`;
- secure adaptive clipping is disabled;
- the threshold must be reached before the session's existing masked-update
  deadline/expiry policy aborts the session.

Sample-level, user-level, hybrid DP, secure adaptive clipping, multiple
simultaneous dropouts, and post-deadline recovery are not silently enabled by
this slice.

## Persistence boundary

Raw recovery shares and reconstructed private keys are never written to the
coordinator's durable stores. The current C++ live adapter keeps recovery state
only in process memory; after coordinator restart, surviving holders must
resubmit their shares. The Python recovery primitive separately supports
commitment-only receipts and is retained as the reference design for any future
durable recovery metadata integration.

## Deferred work

This slice does **not** implement secure pre-dropout share distribution. A
production-capable protocol still needs an authenticated and confidential
holder-delivery/relay mechanism so each holder possesses its assigned share
before a participant disappears. Recovery RPC availability alone is not a
share-distribution protocol.

The existing provider enum still carries the historical
`SECAGG_NO_DROPOUT_EXPERIMENTAL` name. This recovery slice does not rename that
wire value in-place because doing so would create cross-language compatibility
risk. A distinct versioned recovery-capable provider/configuration negotiation
must be introduced before v3.0.0 can claim a stable dropout-recovery provider.

## Release boundary

Presence of this code is not sufficient to close the v3 secure-aggregation
release gate. Required evidence still includes:

- generated-proto compatibility across C++, Python and Go;
- full gRPC coordinator build/test success;
- authenticated multi-process dropout/recovery integration tests;
- crash/restart behavior validation;
- malicious/conflicting share tests at the live RPC boundary;
- explicit provider/configuration negotiation;
- secure share distribution/relay validation;
- interaction validation for any privacy mode enabled in a future slice.
