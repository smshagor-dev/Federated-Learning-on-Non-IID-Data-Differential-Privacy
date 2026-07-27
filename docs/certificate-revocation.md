# Certificate Revocation

**Status: Application-level only, unchanged in substance from the
prior slice, now reachable at more RPCs.** No new certificate-
revocation logic was added this slice — this document exists to state
plainly, in one place, what does and does not happen, per the standing
requirement not to claim handshake-level revocation when only
application-level checking exists.

## What actually happens

* **TLS-handshake-level CRL enforcement: not implemented.** The gRPC/
  OpenSSL mTLS handshake (`transport_credentials.cpp`) validates the
  certificate chain against the trusted CA and checks expiry, but does
  **not** consult `certs/dev/ca/crl.pem` (the file `scripts/pki/revoke-cert.sh`
  produces) during the handshake itself. A certificate revoked via
  `revoke-cert.sh` can still complete a real TLS handshake — the
  coordinator has no OpenSSL-level CRL/OCSP checking wired in.
* **Application-level fingerprint/status checking: implemented**, via
  `WorkerIdentityRegistry`. On `RegisterWorker`, `Heartbeat`, and
  `AcquireTask`, the coordinator checks `registration_status` (not a
  literal "fingerprint revocation list," but the same practical effect
  for a worker whose identity record has been explicitly `REVOKED`)
  against the record already bound to that `certificate_fingerprint`
  at registration time. This is what `RevokeWorker` actually revokes —
  a coordinator-side registry entry, not a CA-level certificate
  revocation.

## The distinction, stated plainly

A worker whose certificate is compromised must be revoked **twice**
for full protection in this system as it stands: once via
`scripts/pki/revoke-cert.sh` (so a *future* CA validation elsewhere
would see it as revoked, and so its CRL entry exists for audit/
compliance purposes) and once via the new `RevokeWorker` RPC (so
*this* coordinator immediately stops trusting anything it
authenticates as that worker_id, regardless of whether the certificate
itself is still technically presentable). Neither one automatically
triggers the other. This is the same gap the prior slice's
`worker-identity-registry.md` already flagged ("Certificate revocation
via `scripts/pki/revoke-cert.sh` only updates the CA's own bookkeeping;
nothing coordinator-side observes it") — unresolved by this slice,
carried forward honestly rather than silently claimed fixed.

## What this slice actually added

Only the *reach* of the existing application-level check: `Heartbeat`
and `AcquireTask` now also check `WorkerIdentityRegistry` status
(previously only `RegisterWorker` did). No new fingerprint-comparison
logic, no CRL parsing, no OCSP integration.

## What remains deferred

* TLS-handshake-level CRL/OCSP checking.
* `SubmitClientResult`/`ReportTaskProgress` checking registry status at
  all (see [worker-revocation.md](worker-revocation.md)'s "What is
  deferred").
* Any automated bridge between `scripts/pki/revoke-cert.sh`'s CRL
  output and `WorkerIdentityRegistry`'s `REVOKED` status.
