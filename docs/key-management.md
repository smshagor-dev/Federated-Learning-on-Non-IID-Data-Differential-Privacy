# Key Management

**Status: mostly Deferred.** This document records what exists today
and what the target design is — most of it is not implemented.

## What exists (Implemented and Validated)

* **Development PKI certificate rotation/revocation** — see
  [development-pki.md](development-pki.md). `scripts/pki/revoke-cert.sh`/
  `.ps1` revokes a certificate and regenerates the CA's CRL; re-issuing
  a certificate for the same identity after revocation is a normal
  `issue-service-cert`/`issue-worker-cert` call. This is PKI-layer
  bookkeeping only — no running coordinator observes or enforces it
  yet (see below).
* **Ed25519 signing-key generation and persistence** — see
  [worker-identity.md](worker-identity.md). One key per `generate_signing_identity`
  call; never reused across worker IDs by default.

## What is deferred

* **Coordinator-enforced certificate/key revocation.** Revoking a
  certificate at the PKI layer does not currently cause a running
  coordinator to reject connections from a worker/service still
  presenting that certificate — there is no coordinator-side identity
  registry (Work Package G) to check against. A revoked certificate is
  only actually rejected once mTLS peer verification independently
  notices the CRL (not wired into either the Go client or the — locally
  unverified — C++ server credential construction this pass).
* **Signing-key rotation workflow** (the "active worker authenticates
  using current identity → submits signed rotation request →
  coordinator validates old identity → new public key registered → old
  key enters grace period → grace period expires → old key rejected"
  flow described in the parent specification). No code implements any
  step of this flow.
* **Worker suspension/activation/revocation RPCs or state.**
* **Grace-period handling for in-flight tasks during a rotation or
  revocation event.**
* **Automated certificate expiry monitoring/alerting** beyond
  `inspect-certificates.py`'s one-shot, manually-invoked expiry report.

## Recommended next steps

See [transport-identity-report.md](transport-identity-report.md)'s
recommended continuation order — the coordinator-side worker identity
registry is the prerequisite most of this document's deferred items
depend on, and should be built before key-rotation/revocation workflows
are attempted, since there is nothing to check a rotation or revocation
request against otherwise.
