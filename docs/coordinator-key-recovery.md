# Coordinator Signing-Key Recovery

**Status: implemented (a command-line administration tool, per the
specification's explicitly-accepted alternative to a full recovery
API) and live-validated for every listed scenario.**

## The tool: `fl_coordinator_key_admin_cli`

`cpp/coordinator/tools/coordinator_key_admin_cli.cpp` — operates
directly on the coordinator's persisted files
(`CoordinatorSigningKeyRegistry`, the keyed private-key directory, and
the trusted-key bundle). No running gRPC server, no network
connection required. Protobuf-free itself, but still needs real
Ed25519 crypto (`coordinator_signing_identity.cpp`), so it is built
alongside the gRPC-gated targets (this Windows/MSVC development
machine has no OpenSSL discoverable outside that build branch either —
confirmed, not assumed).

```
fl_coordinator_key_admin_cli show               --registry-path <path>
fl_coordinator_key_admin_cli rotate             --registry-path <path> --key-dir <dir> \
                                                 [--bundle-path <path>] [--expected-current-key-id <id>] \
                                                 [--grace-period-seconds <n>] [--expires-in-seconds <n>] \
                                                 [--reason <text>]
fl_coordinator_key_admin_cli revoke             --registry-path <path> --key-id <id> \
                                                 [--bundle-path <path>] [--reason <text>]
fl_coordinator_key_admin_cli regenerate-bundle  --registry-path <path> --bundle-path <path>
```

## Recovery scenarios

| Scenario | Operator action | What happens |
|---|---|---|
| **Lost active private key** (the keyed `.pem` file for the current ACTIVE key is gone/corrupted, but the registry still lists it ACTIVE) | `rotate` with no `--expected-current-key-id` | The tool finds no valid current key to rotate *from* (the registry's ACTIVE entry can't actually be loaded/used for signing), falls back to registering a fresh key as a new **initial** key rather than a rotation. The lost key's registry entry is left as-is (still shows ACTIVE in history, honestly reflecting that this was an operator-forced recovery, not a clean rotation) — see "Known caveat" below. |
| **Corrupted key metadata** (the registry file itself fails its checksum) | Restore from backup, or delete and let `show`/`rotate` recreate an empty registry, then `rotate` to establish a fresh initial key | `CoordinatorSigningKeyRegistry`'s constructor throws on a corrupt file rather than silently starting empty — an operator must explicitly move the corrupt file aside first (a deliberate, visible action), then rerun. |
| **Corrupted trusted bundle** | `regenerate-bundle` | Rewrites the bundle from the registry's current (valid) state — the registry, not the bundle, is the source of truth; the bundle is always a derived, regeneratable artifact. |
| **Expired active key** | `rotate` | `validate_rotation` rejects rotating *from* an already-expired key (`kCurrentKeyNotActive`); pass no `--expected-current-key-id` (or one that no longer resolves to an ACTIVE record) to trigger the same initial-key recovery fallback as "lost active private key" above. |
| **Revoked only active key** | `rotate` | Identical fallback: no ACTIVE key exists, so the tool registers a fresh initial key. Live-validated: see [security-administration-report.md](security-administration-report.md). |

## Recovery requirements met

* **Explicit operator action** — every subcommand requires an
  explicit, human-invoked command; nothing here runs automatically.
* **Administration authorization** — enforced at the RPC layer
  (`RotateCoordinatorSigningKey`/`RevokeCoordinatorSigningKey` require
  a go-api service identity) for the live-server path; the CLI itself
  runs with whatever filesystem access its operator already has (the
  same trust model as direct file-based recovery tools throughout this
  codebase, e.g. `scripts/pki/`).
* **New key provisioning** — real OpenSSL Ed25519 keygen
  (`generate_coordinator_signing_identity`), never a placeholder.
* **New bundle generation** — `--bundle-path` regenerates the bundle
  in the same call, or `regenerate-bundle` does it standalone.
* **Clear audit record** — every mutation is still recorded in
  `CoordinatorSigningKeyRegistry`'s persisted, restart-safe file
  (`registration_source`-equivalent context is in this tool's own
  stdout output; see [known-limitations.md](known-limitations.md) for
  why a *durable*, queryable audit journal entry for CLI-driven
  recovery specifically is not implemented this pass).
* **No automatic acceptance of untrusted keys** — the tool only ever
  registers keys it itself just generated with real OpenSSL keygen;
  there is no "import an externally-provided public key" path (out of
  scope, consistent with "no custom encrypted key-vault format").
* **Existing historical public metadata retained** — confirmed live: a
  revoked or expired key's record (including `revoked_at_unix_s`/
  `revocation_reason`) remains visible in `show`'s output indefinitely.
* **Outstanding task handling** — a task signed by a key that becomes
  unavailable in the trusted bundle (expired/revoked) is rejected by
  the worker's own verification pipeline
  (`UNKNOWN_SIGNING_KEY`/`REVOKED_SIGNING_KEY`/`EXPIRED_SIGNING_KEY`);
  there is no coordinator-side "re-sign outstanding tasks" mechanism —
  a worker holding such a task must have its lease expire and be
  reissued under the new key.
* **Workers require the new trusted bundle before accepting new
  tasks** — enforced by the verification pipeline itself; a worker
  that has not reloaded still trusts only its previously-loaded bundle
  (safe: it will reject a task signed by a key genuinely not in that
  bundle, rather than silently trusting an unknown key).

## Known, honestly-disclosed caveat

When recovering from a **lost** (not revoked) active private key, the
registry's OLD entry for that key is left in whatever state it was in
(usually still `ACTIVE`, since nothing told the registry the key was
lost) — the tool does not attempt to mark it `REVOKED` automatically,
since a lost-key situation genuinely doesn't know whether the key
might still be recoverable, and unilaterally revoking it could be the
wrong call in some operational scenarios. An operator who is certain
the old key is gone for good should explicitly `revoke` it after
recovering with a new one, so the registry (and therefore the trusted
bundle, until its own natural expiry) accurately reflects that it
should never be trusted again either.

## Live validation

All five scenarios above were exercised for real in a Docker build,
against a real `CoordinatorSigningKeyRegistry` file and a real
Ed25519-backed keyed-identity directory — see
[security-administration-report.md](security-administration-report.md)'s
"Live runtime results" section for the exact command sequence and
observed output.

## What is deferred

* No durable audit-journal entry specifically for CLI-driven recovery
  actions (the registry file itself is the durable record; there is no
  separate append-only audit log this pass — see
  [known-limitations.md](known-limitations.md)).
* No automated backup/restore tooling for the registry or keyed
  private-key directory — an operator is expected to use standard
  filesystem/volume backup practices for these files, same as every
  other persistence file in this codebase.
