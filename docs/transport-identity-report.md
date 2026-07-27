# Secure Transport and Worker Identity Hardening: Slice Report

**This is the closing report for two stacked implementation slices
within the Secure Aggregation and Cryptographic Protocols category, not
a claim that the full secure-aggregation protocol is complete.**
Pairwise masking, secret sharing, dropout recovery, and secure
aggregate recovery remain explicitly deferred and untouched — see
[secure-aggregation-report.md](secure-aggregation-report.md) for the
category-level status this builds on. Sections 1–9 and 12–39 below are
the original **Secure Transport and Worker Identity Hardening** slice
report (written, unmodified, before any C++ gRPC code had ever been
compiled). Sections marked **[Updated]** have been rewritten to reflect
what the follow-on **Coordinator Transport Verification and Message
Authenticity** slice actually built and, critically, actually *tested
for real* — see the new §§40–46 for that slice's own detailed account.

## 1. Repository audit

Verified from source at the start of this pass (not assumed from
documentation): the C++ `SecureRandomProvider` built in the prior slice
existed but was not connected to the live noise path
(`run_manager.cpp` still constructed `SecureNoiseProvider`); all three
languages' gRPC credential construction had an identical, explicit
`NotImplementedError`/error-return stub for the non-insecure path
(`coordinator_client.py`'s `insecure=False` raised `NotImplementedError`;
Go's `NewGrpcClient` returned `ErrUnavailable` for `Insecure: false`;
C++'s `main.cpp` hardcoded `grpc::InsecureServerCredentials()`
unconditionally); the pre-existing `fl_platform/security/` scaffold
(HMAC envelope signing, in-memory nonce guard, in-memory audit log) was
confirmed still disconnected from any live code path; generated Python
protobuf bindings were confirmed present and importable in this
environment (enabling real local gRPC server/client tests), unlike the
C++ gRPC toolchain (confirmed still absent).

## 2. Previous implementation-slice verification

Re-ran the full validation suite inherited from the prior slice before
making any change: Python 154/154 (at that point), C++ 7/7 CTest suites
(both Debug and Release), Go build/vet/test clean, proto contracts
clean, terminology check clean. All green — nothing regressed before
this pass's own work began.

## 3. Cryptographic dependencies

* **PyNaCl** (`>=1.5,<2`) and **`cryptography`** (`>=42,<50`) — both
  verified installable via `pip install` (prebuilt wheels, no local
  compiler needed) and added to `python/pyproject.toml`'s new
  `security` optional-dependency group and to the repo-root
  `requirements.txt` (used by CI/Docker).
* **libsodium** (C++) — selection unchanged from the prior slice
  (documented in [cryptographic-primitives.md](cryptographic-primitives.md));
  still not linked into the CMake build. All C++ cryptographic work
  through the original slice (`SecureRandomProvider`/`CryptoSecureNoiseProvider`,
  TLS credential construction) used only the C++ standard library and
  gRPC's own bundled SSL support. The follow-on slice (§40 below) added
  a real **OpenSSL** dependency (`find_package(OpenSSL REQUIRED)`,
  gRPC-gated only) for Ed25519 signature verification and SHA-256
  hashing — introducing no *new* dependency in practice, since
  `gRPC::grpc++` already links OpenSSL transitively for its own TLS
  implementation.
* **Go** — standard library only (`crypto/tls`, `crypto/x509`,
  `google.golang.org/grpc/credentials`), as directed. No new third-party
  Go dependency was added.

## 4. Secure-random runtime integration

See [secure-random-runtime.md](secure-random-runtime.md) for full
detail. `CryptoSecureNoiseProvider` is what `RunInstance` actually
constructs for user-level DP's central Gaussian noise and adaptive
clipping's privatized count, replacing `SecureNoiseProvider` on the
live, non-deterministic path. `DeterministicSecureRandomProvider`/
`DeterministicNoiseProvider` remain test-only. `OsEntropySecureRandomProvider`
buffers real OS-CSPRNG bytes (4096-byte chunks, refilled on exhaustion)
rather than issuing one syscall per scalar.

## 5. Privacy-noise changes

No change to the noise *distribution* or *formula* — Box-Muller over
two secure uniforms, exactly matching the interface `SecureNoiseProvider`
already exposed. The change is entirely in *where the randomness comes
from*, confirmed by the full `fl_coordinator_tests` suite staying green
throughout.

## 6. Development PKI

See [development-pki.md](development-pki.md). `scripts/pki/` — now 6
script pairs (bash + PowerShell), the newest being `verify-pki.sh`/`.ps1`
(§41 below) — all executed end-to-end for real: CA generation, service
certificate issuance (coordinator, go-api), worker certificate
issuance, revocation with CRL regeneration, and Python-based
certificate inspection. Five real, non-obvious platform issues were
found and fixed while building the original five (a broken system
`openssl.cnf`, Git-Bash argv path-mangling, a relative-path CA config
bug, PowerShell's missing `openssl` on `PATH` by default, and Windows
PowerShell 5.1's non-UTF-8 script encoding); a sixth (`docker run -e
VAR=/path` env values being MSYS-path-mangled) was found and fixed
during the follow-on slice's live Docker validation work — see
[development-pki.md](development-pki.md).

## 7. C++ mTLS **[Updated]**

`transport_credentials.hpp`/`.cpp` implement `INSECURE_DEVELOPMENT`/
`TLS`/`MTLS_REQUIRED` credential construction against the documented,
stable gRPC C++ SSL API. Wired into `main.cpp` with the same
environment-variable contract as Go/Python. **Now compiled and
runtime-validated for real** — see §40. It compiled successfully on its
very first real Docker build attempt in the follow-on slice, and its
documented transport-mode contract (fail-closed with no env vars,
insecure-with-opt-in, real mTLS with mounted dev-PKI certificates) was
verified against a live running container, not just read.

## 8. Python worker mTLS

`fl_platform/security/transport.py` — `build_channel_credentials`/
`build_secure_channel` via `grpc.ssl_channel_credentials`/
`grpc.secure_channel`. Wired into `GrpcCoordinatorClient.__init__`.
Validated via a real local mTLS RPC round trip in the original slice,
and re-used extensively in the follow-on slice for real RPCs against
the actual containerized C++ coordinator (§40, §42–43).

## 9. Go coordinator-client mTLS

`internal/coordinator/transport.go` — `buildTLSConfig`/
`buildTransportCredentials` via `crypto/tls` + `credentials.NewTLS`.
Wired into `NewGrpcClient` and `cmd/api/main.go`'s
`coordinatorConfigFromEnv`. Validated via a real local mTLS handshake
in the original slice, and re-used in the follow-on slice for a real
`Health` RPC against the live containerized C++ coordinator over
genuine mTLS (a throwaway `go/cmd/mtlstest/main.go` program, built,
run, then deleted).

## 10. Worker identity registry **[Updated — now implemented]**

Implemented and validated: `WorkerIdentityRegistry`
(`cpp/coordinator/include/fl_coordinator/worker_identity_registry.hpp`,
`.cpp`) — filesystem-backed, restart-safe, thread-safe, with the full
specified `WorkerIdentityRecord` field set and `PENDING`/`ACTIVE`/
`SUSPENDED`/`REVOKED`/`EXPIRED` status machine. See
[worker-identity-registry.md](worker-identity-registry.md) and §40/§42
below for the standalone-test and live-wiring validation respectively.

## 11. Certificate identity binding **[Updated — now implemented]**

Implemented and validated live: `peer_identity.hpp`/`.cpp` extracts
`AuthContext`-verified peer identity (URI SAN, CN, and — added in the
follow-on slice — a PEM-text SHA-256 fingerprint) and
`RegisterWorker` rejects a claimed `worker_id` that doesn't match the
authenticated certificate's URI SAN. See
[certificate-identity-binding.md](certificate-identity-binding.md) for
the full account, including three real accept/reject scenarios
exercised against a live container.

## 12. Signing-key management

Implemented and validated at the generation/persistence/sign/verify
level: `fl_platform/security/signing_identity.py` —
`generate_signing_identity`, `save_signing_identity`, `load_signing_identity`,
Ed25519 sign/verify via PyNaCl. Coordinator-side signing-key *rotation*
(as opposed to raw key generation, and as opposed to the follow-on
slice's default-deny-on-mismatch behavior — see §42) remains
unimplemented — see [signing-key-management.md](signing-key-management.md)/
[key-management.md](key-management.md).

## 13. Canonical serialization **[Updated]**

The sorted-key, compact-separator, ASCII-escaped JSON rule for
capability statements is now **cross-language-parity-tested**
(Python ↔ C++), not Python-only — see
[canonical-security-serialization.md](canonical-security-serialization.md)
for the full field-by-field accounting, including how the parity claim
was actually proven (a real Python-generated golden vector embedded in
a C++ test, plus a live end-to-end signature verification that could
only pass if both encoders agreed byte-for-byte). The previously-flagged
domain-separation-prefix gap remains open.

## 14. Signed capabilities **[Updated — now wired live]**

`fl_platform/security/capability_statement.py` (signing side) and the
new `cpp/coordinator/src/capability_statement_verifier.cpp` (verifying
side) are both implemented, tested, and now **wired into the live
`RegisterWorker` RPC** — see [signed-capabilities.md](signed-capabilities.md)
and §42–43 below for the full live-container validation (7 real
end-to-end scenarios, including a real container restart to confirm
`WorkerIdentityRegistry` persistence).

## 15. Signed envelopes

**Not implemented.** See [signed-worker-envelopes.md](signed-worker-envelopes.md).
Still out of scope after the follow-on slice — only capability
statements are signed and verified end-to-end; heartbeat, task
acceptance/progress, client result, task failure, sample-level privacy
ledger entries, personalized metrics, and worker shutdown messages
remain unsigned.

## 16. Replay protection

**Not implemented** beyond the pre-existing, disconnected
`NonceReplayGuard` scaffold. See [replay-protection.md](replay-protection.md).
A signed capability statement's `nonce` field is verified for shape but
never checked against a store of previously-seen nonces — a captured,
still-unexpired statement could be replayed.

## 17. Key rotation

**Not implemented.** A worker presenting a different signing key than
the one already on record for its `worker_id` is unconditionally
rejected (default-deny) — see §42 — but there is no sanctioned rotation
flow with a grace period. See [key-management.md](key-management.md).

## 18. Worker revocation **[Updated]**

`WorkerIdentityRegistry::revoke` is implemented and tested (terminal,
idempotent, blocks re-registration) — but **only `RegisterWorker`
consults the registry**. No other RPC (`AcquireTask`,
`SubmitClientResult`, `Heartbeat`) checks registration status yet, so a
revoked worker mid-task is not yet actually cut off from continuing
that task. PKI-layer certificate revocation
(`scripts/pki/revoke-cert.sh`) still updates only the CA's own
bookkeeping and is not cross-checked against the running coordinator.

## 19. Protobuf changes **[Updated]**

The follow-on slice added one new message,
`fl.worker.v1.SignedCapabilityStatement` (24 fields, schema-versioned),
and one new field, `RegisterWorkerRequest.signed_capability` (field 4,
additive — every prior field number preserved). Verified via
`scripts/verify_proto_contracts.py`, which passed both before and after
the change.

## 20. Go security APIs

**Not implemented.** None of the `/api/v1/security/*` endpoints from
the parent specification exist yet.

## 21. Web security views

**Not implemented.**

## 22. Security events

**Not implemented** as a dedicated event taxonomy.

## 23. Metrics

**Not implemented.**

## 24. Audit records

**Not implemented** beyond the pre-existing, disconnected
`AuditLog`/`AuditEvent` scaffold.

## 25. Files added (original slice)

C++: `secure_random.hpp`/`.cpp` (extended), `secure_random_test.cpp`
(extended), `transport_credentials.hpp`/`.cpp` (new).
Python: `security/transport.py`, `signing_identity.py`,
`capability_statement.py` (new); 4 new/extended test files.
Go: `internal/coordinator/transport.go`, `transport_test.go`,
`cmd/api/main_test.go` (new).
Scripts: `scripts/pki/` — original 9 files.
Docs: 10 new files.

See §44 below for the follow-on slice's own files-added/modified list.

## 26. Files modified (original slice)

See original commit history; superseded in relevant part by §44 below.

## 27. Tests added (original slice)

C++: 2 new test groups (still 7/7 CTest suites). Python: 46 new tests
(193 passed / 1 skipped repo-wide at that point). Go: 18 new tests.

## 28. Exact commands executed (original slice)

```bash
git status
python scripts/check_project_terminology.py
python -m pytest -q
python -m ruff check . && python -m ruff format --check .
python -m mypy --config-file=python/pyproject.toml python/src
cmake --build build/cpp-debug --config Debug
ctest --test-dir build/cpp-debug -C Debug --output-on-failure
cmake --build build/cpp-release --config Release
ctest --test-dir build/cpp-release -C Release --output-on-failure
cd go && gofmt -l . && go vet ./... && go build ./... && go test ./...
python scripts/verify_proto_contracts.py
bash scripts/pki/generate-dev-ca.sh
bash scripts/pki/issue-service-cert.sh coordinator
bash scripts/pki/issue-worker-cert.sh worker-1/worker-2
```

See §45 for the follow-on slice's own command list.

## 29. Pass, fail, or blocked status (original slice)

All of §1–9/12–28 above: **pass**. C++ TLS credential code was
explicitly **not** included in any "pass" claim in the original slice —
stated as untested at that time. This is precisely the gap the
follow-on slice closed — see §40.

## 30. Cross-language signature results **[Updated]**

Now applicable and passing: Ed25519 signing (Python, PyNaCl) verified
by C++ (OpenSSL EVP), over canonical JSON bytes independently produced
by each language's own encoder, both live over real mTLS and in a
dedicated C++ unit test against a real Python-produced golden vector.
See §13 and [canonical-security-serialization.md](canonical-security-serialization.md).
No Go implementation exists.

## 31. Docker mTLS results **[Updated]**

**Now run, extensively, for real** — see §40–43. This was the single
largest gap the original slice flagged; it is now closed for the
scenarios listed there. The full 26-scenario Docker Compose validation
from the parent specification's Work Package Z has **not** been run —
what was validated is direct `docker run` against the single
`fl-coordinator` container with mounted dev-PKI certificates, real Go/
Python clients dialing in, not a multi-service Compose stack exercising
non-private/sample-private/user-private/hybrid-private runs end-to-end.

## 32. Performance methodology (original slice)

Real, locally-measured wall-clock comparison of `SecureNoiseProvider`
vs. `CryptoSecureNoiseProvider` — see
[secure-random-runtime.md](secure-random-runtime.md). No new
performance benchmarking was done in the follow-on slice (TLS
handshake latency, certificate validation, Ed25519 verify throughput,
identity-registry lookup, etc. from the parent specification's Work
Package remain unbenchmarked).

## 33. Performance results (original slice)

Release: ~1.5×–1.8× overhead (0.032–0.037 µs/sample →
0.050–0.066 µs/sample), both sub-microsecond. No other primitive was
benchmarked in either slice.

## 34. Security findings **[Updated]**

Original slice: a genuine bug found while *writing a test* (Go's x509
hostname verification needing an IP SAN, not just DNS, for a numeric
`ServerName`) — not a production-code bug. Follow-on slice: **zero
production-code bugs found by testing** — every new C++ module
(`peer_identity.cpp`, `worker_identity_registry.cpp`,
`capability_statement_verifier.cpp`) compiled and passed its tests on
the first real attempt. All bugs encountered in the follow-on slice
were test-harness/environment issues (MSYS path mangling on `docker run
-e`, a Docker container-lifecycle mistake building only 2 of 8 CTest
targets, a Go `internal/` package import-boundary mistake in a
throwaway test program) — documented in
[development-pki.md](development-pki.md) and inline where relevant, not
hidden.

## 35. Remaining trust assumptions **[Updated]**

Everything in [secure-aggregation-threat-model.md](secure-aggregation-threat-model.md)
still holds, plus:

* A signed capability statement authenticates *who* made a claim, never
  *whether the claim is true* — see [signed-capabilities.md](signed-capabilities.md).
* mTLS + certificate identity binding + the worker identity registry
  together now establish "this `worker_id`'s `RegisterWorker` call came
  from a certificate matching that identity, signed by the same
  Ed25519 key it registered with previously" — but **nothing enforces
  this for any RPC other than `RegisterWorker`**. A worker that
  registered legitimately and was later revoked can still call
  `AcquireTask`/`SubmitClientResult`/`Heartbeat` freely; only a repeat
  `RegisterWorker` call would be blocked.
* `WorkerIdentityRegistry`'s revocation is **application-level only** —
  it is not integrated with TLS-stack certificate revocation (no
  OCSP/CRL check during the handshake itself). See
  [worker-identity-registry.md](worker-identity-registry.md).
* Nonce replay protection does not exist — a captured, still-valid
  signed capability statement can be replayed verbatim.

## 36. Known limitations

See [known-limitations.md](known-limitations.md).

## 37. Regression status **[Updated]**

Zero regressions in either slice. Follow-on slice: C++ CTest suite grew
from 7 (Debug/Release) to 10 (Debug/Release; the 3 new suites are
`fl_coordinator_grpc_tests`, `fl_peer_identity_tests`,
`fl_capability_statement_verifier_tests`, plus `fl_coordinator_tests`
gained one more internal test group), all passing at 100% both locally
(MSVC, non-gRPC-gated targets) and in Docker (all 10, including the
gRPC-gated ones, for the first time ever compiled and run together).
Python stayed at 193 passed / 1 skipped (no new Python tests were added
this slice — the new coverage is C++ unit tests plus a live end-to-end
script, not new `pytest` files). Terminology and proto-contract checks
passed before and after every change.

## 38. Git working-tree summary

No commits were made in either slice — per standing instructions, work
is not committed, pushed, tagged, or opened as a pull request without
an explicit request.

## 39. Recommended next scope (superseded by §46)

See §46 below for the current, up-to-date recommendation — the original
slice's §39 recommended exactly the work the follow-on slice then
completed (Docker validation, worker identity registry, signed
capability wiring, cross-language canonical serialization).

---

# Coordinator Transport Verification and Message Authenticity slice

## 40. C++ gRPC coordinator: compiled and runtime-validated for the first time

Built via `infra/docker/cpp-coordinator.Dockerfile` — the only
environment in this project's history where the gRPC-gated C++ targets
(`fl_coordinator_grpc_server`, `fl_coordinator_grpc_tests`) have ever
been compiled, since this development machine has no local C++ gRPC
toolchain. `transport_credentials.cpp` (written blind in the prior
slice) compiled successfully on the **first** real build attempt.
Runtime-validated against the documented transport-mode contract:

* No environment variables set → fails closed, exit code 1, structured
  stderr error.
* `FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT=true` → starts correctly in
  insecure mode.
* Real mTLS mode with mounted dev-PKI certificates
  (`FL_TRANSPORT_MODE=mtls`, `FL_COORDINATOR_SERVER_CERT`/`_KEY`,
  `FL_COORDINATOR_CLIENT_CA`) → starts correctly, logs
  `transport_mode=mtls`.

Real cross-language mTLS validated against the live container:

* **Python**: `fl_platform.security.transport.build_secure_channel` +
  `coordinator_pb2_grpc.CoordinatorServiceStub` — a real `Health` RPC
  succeeds over genuine mTLS.
* **Go**: `internal/coordinator.NewGrpcClient` + `client.Health(ctx)` —
  succeeds over genuine mTLS via a throwaway `go/cmd/mtlstest/main.go`
  (built inside the Go module to satisfy its `internal/` import
  boundary, run, then deleted).
* **Rejection paths**: a client presenting no certificate is rejected
  (`UNAVAILABLE`); a client presenting a certificate from an untrusted
  CA is rejected the same way.

## 41. Automated PKI verification (Work Package D)

`scripts/pki/verify-pki.sh`/`.ps1` (new) automate the full PKI
lifecycle against a throwaway CA (never touching `certs/dev/`): CA
generation → issue coordinator/go-api/worker-1/worker-2 certificates →
inspect URI SANs → validate each chain against the CA → revoke worker-2
→ regenerate the CRL → confirm `openssl verify -crl_check` now rejects
worker-2 but still accepts worker-1 → confirm no `certs/dev*`/`*.key.pem`
paths are Git-tracked → delete all private key material via an exit
trap (bash) / `finally` block (PowerShell), so cleanup runs even on
failure. Both variants run for real on this machine: **21/21 checks
pass**, bash and PowerShell independently. Wired into CI as a new
`pki-verify` job and into `Makefile` as `make pki-verify`.

## 42. Worker identity registry + signed capability statements: implemented and wired live

`WorkerIdentityRegistry` (filesystem-backed, restart-safe, thread-safe,
atomic-write persistence following the same temp-file-then-rename
pattern as `AggregatorCheckpointStore`) is implemented with the full
specified field set and status machine, and its own dedicated test
suite (`worker_identity_registry_test.cpp`) passes locally via MSVC:
persistence-across-restart, idempotent re-registration,
certificate-fingerprint uniqueness, the full
suspend/activate/revoke state machine (including revocation being
terminal and idempotent-without-overwriting-the-original-reason),
expiry sweeping, and corruption detection (a truncated file is rejected,
never silently treated as empty).

`capability_statement_verifier.cpp` (new, OpenSSL-backed, gRPC-gated
build only) implements Ed25519 verification and a canonical-JSON
encoder that byte-matches Python's `json.dumps(sort_keys=True,
separators=(",",":"), ensure_ascii=True)` — proven against a real
Python-produced golden vector embedded in
`capability_statement_verifier_test.cpp`, plus a full sign/verify round
trip (valid, tampered, expired, wrong-key, unset-expiry — all correctly
accepted/rejected).

Both are wired into `RegisterWorker` (`coordinator_service.cpp`):
verify payload hash → verify Ed25519 signature → verify expiry → verify
the payload's `worker_id` matches the request's → check the identity
registry for an existing, possibly-revoked, possibly-different-signing-key
record → register/refresh, but only once the connection is actually
mTLS-authenticated (so the registry's uniqueness key, a certificate
fingerprint, is a real value). `CoordinatorServiceImpl`'s constructor
gained an optional second parameter (`WorkerIdentityRegistry* = nullptr`)
so every pre-existing call site (`coordinator_service_test.cpp`,
single-argument) kept compiling and behaving identically; `main.cpp`
now constructs and passes a real, file-backed instance
(`FL_WORKER_IDENTITY_REGISTRY_PATH`, default `worker_identity_registry.dat`).

**Live, containerized, real-mTLS, real-Ed25519 end-to-end validation**
(a throwaway Python script, not a unit test double), all seven
scenarios passing against a real running container:

1. A validly signed, non-expired statement is accepted.
2. Re-registering with the *same* signing key succeeds (idempotent).
3. Re-registering the *same* `worker_id` with a *different* signing key
   is rejected `PERMISSION_DENIED` — proven against the container's
   **actual persisted state from a prior run**, restarted fresh to get
   a clean baseline, then re-proven deliberately.
4. A statement tampered with after signing is rejected, specifically
   reported as a `payload_hash` mismatch (not conflated with a bad
   signature).
5. An expired statement (signed valid-for-2-seconds, sent 3 seconds
   later) is rejected, specifically reported as expired.
6. worker-2's certificate cannot register a signed statement claiming
   `worker_id: worker-1` — rejected by the pre-existing certificate
   identity binding check, before the signature is even inspected.
7. worker-2 registering its own signed statement over its own
   certificate succeeds.
8. (Persistence check, not numbered above) The
   `WorkerIdentityRegistry` file, read directly inside the running
   container via `docker exec cat`, contains both workers' real bound
   identities and signing keys — and the identical content is still
   present after a real `docker restart`.

## 43. Regression validation for this slice

Full `ctest` run inside a throwaway all-targets Dockerfile (building
every target, not just the gRPC-gated ones, in one pass):
**100% tests passed, 0 tests failed out of 10** — the 7 pre-existing
suites plus the 3 new ones
(`fl_coordinator_grpc_tests`/`fl_peer_identity_tests`/
`fl_capability_statement_verifier_tests`). Locally (MSVC, non-gRPC
targets): 7/7. Python: 193 passed, 1 skipped (unchanged — no Python
source or test files were modified this slice). Terminology check and
`verify_proto_contracts.py` both clean before and after every change.

## 44. Files added/modified this slice

**New**: `cpp/coordinator/include/fl_coordinator/peer_identity.hpp`/`.cpp`
(peer identity extraction — written in this slice's earlier portion,
compiled/validated in this slice), `worker_identity_registry.hpp`/`.cpp`,
`capability_statement_verifier.hpp`/`.cpp`;
`cpp/coordinator/tests/peer_identity_test.cpp`,
`worker_identity_registry_test.cpp`, `capability_statement_verifier_test.cpp`;
`scripts/pki/verify-pki.sh`/`.ps1`;
`docs/certificate-identity-binding.md`, `docs/worker-identity-registry.md`.

**Modified**: `cpp/CMakeLists.txt` (new gRPC-gated `find_package(OpenSSL
REQUIRED)`, 3 new test targets, new sources on `fl_coordinator`/
`fl_coordinator_grpc_server`/`fl_coordinator_grpc_tests`);
`cpp/coordinator/src/coordinator_service.cpp` (certificate identity
binding + signed-capability verification + identity-registry wiring in
`RegisterWorker`); `cpp/coordinator/include/fl_coordinator/coordinator_service.hpp`
(optional `WorkerIdentityRegistry*` constructor parameter);
`cpp/coordinator/main.cpp` (constructs and wires a real
`WorkerIdentityRegistry`); `proto/worker/worker.proto`
(`SignedCapabilityStatement` message, `RegisterWorkerRequest.signed_capability`
field, both additive); `Makefile` (`pki-verify` target);
`.github/workflows/ci.yml` (`pki-verify` job); `docs/development-pki.md`,
`docs/signed-capabilities.md`, `docs/canonical-security-serialization.md`
(status updates reflecting live validation), this report.

## 45. Exact commands executed this slice

```bash
docker build -t <tag> -f infra/docker/cpp-coordinator.Dockerfile .   # real build, real success
docker run -d --name <name> -p <port>:50051 -v "$CERTS:/certs:ro" \
  -e FL_TRANSPORT_MODE=mtls -e FL_COORDINATOR_SERVER_CERT=... \
  -e FL_COORDINATOR_SERVER_KEY=... -e FL_COORDINATOR_CLIENT_CA=... <image>
python <real Python Health/RegisterWorker RPC scripts, over real mTLS>
go run ./cmd/mtlstest <ca> <cert> <key>                              # then deleted
bash scripts/pki/verify-pki.sh                                       # 21/21 pass
scripts/pki/verify-pki.ps1                                           # 21/21 pass, PowerShell
cmake --build build/cpp-debug && ctest --test-dir build/cpp-debug -C Debug --output-on-failure  # 7/7
docker build (throwaway, all targets) && ctest --output-on-failure   # 10/10
python scripts/check_project_terminology.py                          # pass, repeatedly
python scripts/verify_proto_contracts.py                             # pass, before and after
python -m pytest tests python/tests -q                               # 193 passed, 1 skipped
docker exec <container> cat /app/worker_identity_registry.dat        # real persisted state, twice, across a restart
```

All commands above: **pass**, exactly as reported, nothing fabricated.
No command in this slice was reported as passing without actually
having been run.

## 46. Recommended next scope

Directly per the Required Implementation Order's own sequencing and
this slice's own findings:

1. **RPC-wide suspension/revocation enforcement** — `WorkerIdentityRegistry`
   exists and blocks re-registration of a revoked worker, but
   `AcquireTask`/`SubmitClientResult`/`Heartbeat` do not yet consult it.
   This is the most security-relevant remaining gap: a revoked worker
   mid-task today is not actually cut off.
2. **Persistent replay protection** for signed capability statements
   (a nonce store, following the same restart-safe pattern as
   `WorkerIdentityRegistry`) — currently a captured, unexpired statement
   can be replayed verbatim.
3. **Signing-key rotation with a grace period** — today, any signing-key
   change is an unconditional rejection; there is no sanctioned way for
   a legitimate worker to rotate its key.
4. **Signed envelopes** for heartbeat/task-result/privacy-ledger
   messages, extending the now-proven canonical-JSON + Ed25519 pattern
   beyond capability statements alone.
5. Go security APIs, web security views, security events/metrics/audit
   records, and full Docker Compose multi-service validation (the
   parent specification's 26 numbered scenarios) all remain entirely
   unstarted.
6. The threshold secret-sharing blocker from the original slice remains
   unresolved and out of scope for all of the above — pairwise masking
   and secret sharing should not begin until it is.

Explicit non-goals maintained throughout both slices, per standing
instruction: no pairwise masking, private masks, fixed-point
secure-aggregation encoding, secret sharing, dropout recovery,
unmasking, protocol transcript chaining, secure aggregate decoding,
homomorphic encryption, Byzantine-robust aggregation, worker
attestation, TEEs, TPM integration, Ray, Flower runtime integration,
asynchronous/semi-synchronous aggregation, or production Kubernetes
rollout. No commits, pushes, tags, or pull requests were made without
explicit request.
