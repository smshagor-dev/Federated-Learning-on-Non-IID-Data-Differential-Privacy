# Trusted Coordinator Key Bundle (Lifecycle)

**Status: implemented, cross-language-verified (a real C++-written
bundle checksum independently re-derived and confirmed by Python), and
live-validated.**

## Format

```json
{
  "schema_version": 1,
  "coordinator_identity": "coordinator",
  "bundle_version": 4,
  "generated_at_unix_s": 1785005386.22,
  "active_signing_key_id": "87377d13efdc1d5a",
  "keys": [
    {
      "signing_key_id": "...",
      "public_key_hex": "...",
      "public_key_fingerprint": "...",
      "status": "active",
      "created_at_unix_s": 0.0,
      "expires_at_unix_s": 0.0,
      "grace_period_end_unix_s": 0.0,
      "revoked_at_unix_s": 0.0
    }
  ],
  "checksum": "..."
}
```

`created_at_unix_s` also serves as the activation timestamp: a
coordinator key is `ACTIVE` immediately upon creation (unlike a worker
key, which can be registered ahead of becoming preferred), so there is
no separate activation event to record.

Only keys currently `ACTIVE` or `GRACE_PERIOD` are ever included
(`CoordinatorSigningKeyRegistry::trusted_public_keys`) — a revoked or
expired key is never listed as currently trusted, even though its full
history remains inspectable via `GetCoordinatorSigningKeys` or the
recovery CLI's `show` subcommand.

## Writer: `cpp/coordinator/src/trusted_key_bundle.cpp`

`write_trusted_key_bundle(registry, path, coordinator_identity_label, now)`:

* Reads whatever `bundle_version` is currently on disk at `path` (0 if
  absent/unparseable — never throws), increments it, and writes a
  brand-new bundle reflecting the registry's current trusted-key set.
* **Atomic**: temp-file + rename, same convention as every other
  persistence class in this codebase — no reader ever observes a
  partially-written bundle.
* **Checksum**: FNV-1a hex over the JSON body (everything up to and
  including its own closing `}`, before the `"checksum"` field and a
  new closing brace are appended) — accidental-corruption detection
  only, **not** a cryptographic integrity guarantee. See "Bundle
  signature" below for why that is an intentional, disclosed scope
  boundary rather than an oversight.
* Called from `main.cpp` at startup and from both
  `RotateCoordinatorSigningKey`/`RevokeCoordinatorSigningKey`'s
  handlers, and from the recovery CLI's `rotate`/`revoke`/
  `regenerate-bundle` subcommands — one writer, one file format, every
  call site.

## Reader: `fl_platform.security.coordinator_trust_bundle`

* `load_trusted_coordinator_key_bundle(path)` — verifies the checksum
  by reconstructing the exact same byte layout the C++ writer produced
  (not by re-serializing the parsed JSON, which could reorder keys or
  reformat numbers differently and would make the "verification" a
  tautology), rejects an unsupported `schema_version`, rejects more
  than one `ACTIVE` key, and only then parses the key list.
* `TrustedCoordinatorKeyBundleReloader` (Work Package F) — a stateful,
  thread-safe wrapper: the *first* load must succeed (there is no
  fallback bundle yet); every subsequent `.reload()` call keeps the
  previous valid bundle unless the candidate (a) passes every
  validation the plain loader performs, **and** (b) has a
  `bundle_version` strictly greater than (or equal to, for a genuine
  no-op) the one currently held. A candidate with a *lower*
  `bundle_version` is rejected as a rollback attempt.

## "Bundle signature or protected-distribution guarantee"

This slice does **not** self-sign the bundle with the coordinator's
own key (which would be circular — the bundle is what tells a worker
which key to trust in the first place) or with a separate bundle-
signing key (out of scope this pass). The protected-distribution
guarantee is the same one [development-pki.md](development-pki.md)
already relies on for the TLS CA certificate: atomic writes, restrictive
file permissions, and out-of-band delivery (a mounted volume/secret,
never fetched over the connection whose authenticity is in question) —
not an additional signature layered on top. Stated honestly in the
header comment of `trusted_key_bundle.hpp`, not silently assumed.

## Live validation

Real Docker build: the recovery CLI wrote four successive real bundle
versions (initial bootstrap, a real rotation, a lazy-evaluated real
expiry after an actual elapsed wait, and a revocation reducing the
trusted-key set to zero) — each one independently loaded and
checksum-verified by a real Python process (`load_trusted_coordinator_key_bundle`,
`TrustedCoordinatorKeyBundleReloader`), confirming byte-for-byte
cross-language agreement on the checksum algorithm, not merely that
each side's own round-trip works. See
[security-administration-report.md](security-administration-report.md).

## What is deferred

* No bundle self-signature or separate bundle-signing key.
* No automated distribution mechanism (e.g. a push notification to
  workers on bundle change) — workers must reload (see
  [signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s worker
  reload integration) on their own schedule.
