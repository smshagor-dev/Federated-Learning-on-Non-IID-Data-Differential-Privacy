# Secure Aggregation and Cryptographic Protocols: Architecture and Design Decision Record

**Status: this document describes the target architecture for the Secure
Aggregation and Cryptographic Protocols category. Most components below
are marked `Deferred` — this pass implements the prerequisite Privacy
Engineering closure-gate work
([sample-level budget enforcement](#closure-gate-sample-level-budget-enforcement-implemented)
and the [secure random provider](#closure-gate-cryptographically-secure-noise-implemented))
and produces the design record, threat model, and dependency selection
that the protocol work depends on. It does not yet implement TLS/mTLS,
worker identity, signed envelopes, the secure aggregation protocol
itself, Go/web integration, or Docker validation of any of those. See
[secure-aggregation-report.md](secure-aggregation-report.md) for the
authoritative status of every individual requirement against this
document's plan.**

Every component below is tagged:

* **Implemented** — real code, real tests, exercised this pass.
* **Designed** — a concrete decision has been made and is documented
  here, but no code exists yet.
* **Deferred** — not designed in detail yet; noted as required future
  work.

## 1. Why this document exists

The Secure Aggregation and Cryptographic Protocols category asks for a
large amount of genuinely new capability: mutual TLS across three
languages, authenticated worker identities, a published secure
aggregation protocol (pairwise masking, threshold secret sharing,
dropout recovery, transcript integrity), and full Go/web/Docker
integration. This is real cryptographic-protocol engineering, not a
config flag — attempting to fabricate a "complete" implementation in a
single pass would violate the same standard this category's own trust
statement holds the *product* to: no unsupported security claims. This
document is the design record required before any protocol code is
written (see the Required Working Method), so that later passes build
against a stated decision rather than an implicit one.

## 2. Starting point (repository audit)

Verified by reading source, not assuming documentation:

* No cryptographic library (libsodium, OpenSSL, PyNaCl, `cryptography`)
  is referenced anywhere in `cpp/CMakeLists.txt` or
  `python/pyproject.toml` before this pass.
* `python/src/fl_platform/security/` already exists, but is a Foundation-phase
  scaffold: `nonce.py` (in-memory replay guard, no persistence),
  `envelope.py` (**HMAC-SHA256 with a shared secret**, not asymmetric —
  does not satisfy this category's Ed25519 worker-identity requirement),
  `audit.py` (in-memory audit log), `secure_aggregation.py` (a config
  *validator* with no cryptography — `enabled`/`minimum_cohort_size`/
  `dropout_recovery` fields and warning strings only). None of these are
  imported by the real worker/coordinator pipeline — only by their own
  isolated test file (`python/tests/test_security_foundations.py`).
  They are safe to build alongside without touching a live code path,
  but do not themselves need to be deleted or replaced wholesale; the
  new identity/envelope work lives in the new `secure_aggregation`
  packages this category adds (§9), and the old HMAC envelope is left
  in place, documented as superseded-for-this-purpose rather than
  silently repurposed.
* `cpp/CMakeLists.txt` has no package manager (no vcpkg/Conan) — new
  external dependencies must follow the exact pattern already
  established for gRPC/Protobuf: `find_package(... QUIET)`, build the
  dependent target only when found, skip gracefully with a `message(STATUS ...)`
  on this Windows development machine, and build for real in CI/Docker
  (`ubuntu-latest` via `apt`, or `infra/docker/cpp-coordinator.Dockerfile`).
  This has a direct consequence for this category: `fl_core` itself
  (tensor/aggregation/checkpoint/privacy math) has **no** gRPC
  dependency and builds/tests locally on this machine; anything that
  touches `coordinator_service.cpp`/`main.cpp` (the real gRPC server,
  and therefore mTLS) can only be locally verified for syntax/structure
  and must be built/tested in CI or Docker, exactly like the existing
  `fl_coordinator_grpc_server`/`fl_coordinator_grpc_tests` targets.
* Several documentation filenames referenced in this category's
  instructions do not exist in this repository under those exact names
  (`privacy-engineering-architecture.md`, `privacy-engineering-validation.md`,
  `privacy-modes.md`, `sample-level-dp.md`, `privacy-checkpoint-recovery.md`,
  `security-model.md`). Their content lives in differently-named
  existing docs: [privacy-mathematics.md](privacy-mathematics.md) (sample-level
  math and modes), [privacy-engineering-report.md](privacy-engineering-report.md)
  and [docker-runtime.md](docker-runtime.md) (validation results),
  [privacy-budget-policies.md](privacy-budget-policies.md) (checkpoint/recovery
  of privacy state), [privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)
  and [algorithm-expansion-security-audit.md](algorithm-expansion-security-audit.md)
  (the closest existing equivalents to a security model doc). This is
  noted so a future reader does not assume those files were deleted.

## 3. Provider abstraction — Designed

```cpp
class SecureAggregationProvider {
public:
    virtual ~SecureAggregationProvider() = default;
    virtual ProtocolCapabilities capabilities() const = 0;
    virtual CohortSession create_session(const SecureAggregationConfig&, const CohortDefinition&) = 0;
    virtual ProtocolResult submit_message(const SessionId&, const ProtocolMessage&) = 0;
    virtual SecureAggregateResult finalize(const SessionId&) = 0;
    virtual void abort(const SessionId&, const std::string& reason) = 0;
};
```

Required providers: `NONE` (today's plaintext aggregation path,
unchanged — the only provider that actually exists in runnable code
right now) and `SECAGG_PLUS_NATIVE` (the C++-native Bonawitz/SecAgg+-style
protocol this category targets — **Deferred**, not yet implemented). An
optional `FLOWER_SECAGG_PLUS_REFERENCE` adapter may be added later as a
cross-check/reference backend; it must never become the runtime
authority, matching this project's standing prohibition on Flower as
the main platform runtime. No provider code exists yet; this section
records the interface decision so later work implements against a
stated contract rather than inventing one mid-protocol.

## 4. Protocol selection — Designed

Bonawitz et al.'s Practically Secure Aggregation protocol (and its
SecAgg+ refinement) is selected as the reference protocol family,
per the category's own instruction, over inventing a novel scheme. The
required stage list —
`COHORT_FORMING → IDENTITY_VERIFICATION → KEY_ADVERTISEMENT →
ENCRYPTED_SHARE_DISTRIBUTION → MASKED_UPDATE_COLLECTION →
DROPOUT_RESOLUTION → UNMASKING → AGGREGATE_VALIDATION →
COMPLETED | ABORTED | FAILED` — is adopted as-specified as the target
state machine. **Deferred**: no state machine code exists yet. It will
be implemented as a dedicated C++ module
(`cpp/secure_aggregation/protocol_state_machine.*`) once transport and
identity hardening (mTLS, worker identity) land first, per the Required
Working Method's explicit ordering ("implement cryptographic protocol
components only after transport and identity tests pass").

## 5. Cryptographic dependency selection — see dedicated doc

See [cryptographic-primitives.md](cryptographic-primitives.md) for the
full selection, license, and maintenance-status review. Summary: C++
targets libsodium (Ed25519, X25519, HKDF-SHA-256, XChaCha20-Poly1305,
`randombytes_buf` for OS-backed CSPRNG access), gated behind
`find_package(... QUIET)` exactly like gRPC; Python targets PyNaCl (the
libsodium binding) and the `cryptography` package for parity with the
C++ primitive choices. **Neither is linked/installed yet in this pass**
— dependency *selection* is complete and documented; dependency
*integration* (actually adding `find_package`/`requirements.txt` entries
and writing code against them) is the first item of subsequent work,
per the Required Implementation Order (`6. Implement TLS and mTLS`
onward).

## 6. Closure-gate: sample-level budget enforcement — Implemented

See [privacy-budget-policies.md](privacy-budget-policies.md)'s "Sample-level
DP's budget" section for the gap this closes: prior to this pass,
`SampleLevelDPConfig.epsilon_budget` was informational only. This pass
implements real worker-side enforcement — a distinct, per-task
`SamplePrivacyBudgetPolicy` enum (WARN_ONLY/STOP_BEFORE_EXCEEDING/
STOP_AFTER_CURRENT_TASK/FAIL_TASK), a `SampleBudgetEnforcer` operating
directly against the real Opacus accountant (never a parallel one),
pre-step projection and post-step enforcement wired into
`run_private_local_training`, checkpoint/restore of accountant state
without double-counting, and 24 new tests (unit + real-training
integration, one path per policy). A genuine pre-existing bug was found
and fixed along the way: `PrivacyEngine()` was never given
`accountant=privacy_config.accountant`, so every private-training call
silently used Opacus's default `"prv"` accountant internally regardless
of what was configured/reported — fixed by passing it explicitly. See
`python/src/fl_platform/privacy/budget_enforcement.py` and
[privacy-budget-policies.md](privacy-budget-policies.md) for full detail.

## 7. Closure-gate: cryptographically secure noise — Implemented as a
tested building block; **not yet wired into the live noise-generation
call site**

**C++**: `fl::core::SecureRandomProvider` abstraction
(`cpp/core/include/fl_core/secure_random.hpp`/`.cpp`) — a genuine,
tested improvement over the pre-existing `SecureNoiseProvider` in
`privacy.hpp`: instead of seeding one `mt19937_64` once from
`std::random_device` and reusing it for every element (which is what
`SecureNoiseProvider` still does, and which the standard does not
actually guarantee is non-deterministic), `OsEntropySecureRandomProvider`
draws fresh OS-CSPRNG bytes on *every* call — `BCryptGenRandom` on
Windows, `/dev/urandom` on POSIX — with a `DeterministicSecureRandomProvider`
for tests, provider identity as a real accessor
(`SecureRandomProviderIdentity`), and a hard `SecureRandomUnavailableError`
(not a silent fallback) on entropy-source failure. Built and tested for
real on this machine (`fl_secure_random_tests`, part of the standard
CTest suite, no gRPC dependency) — 6 test groups covering identity,
determinism, OS-entropy variability, uniform/Gaussian distribution
sanity, and buffer-size edge cases.

**What this does *not* do yet**: `run_manager.cpp`'s
`add_central_gaussian_noise` call site (the actual runtime user-level-DP
noise path) still constructs a `SecureNoiseProvider`, not the new
`OsEntropySecureRandomProvider` — this was a deliberate choice this
pass, not an oversight. Swapping it naively would mean one OS syscall
per masked tensor element (`fill_random_bytes` is called twice per
`gaussian_sample` via Box-Muller), which is fine for this project's tiny
synthetic models but would be a severe performance regression for any
real-sized tensor. The correct architecture — seed a ChaCha20-based
stream from OS entropy once per noise-generation call, then stream
values from it, rather than one syscall per double — is exactly the
"ChaCha20-based deterministic mask generation" primitive already
selected in [cryptographic-primitives.md](cryptographic-primitives.md),
and needs libsodium actually linked (§5's documented, not-yet-done
integration step) to implement properly rather than hand-rolling a
stream cipher. Rewiring `finalize_round`'s live noise path — which has
extensive existing tests and live-Docker-validated epsilon values (see
[user-level-dp.md](user-level-dp.md)) — without that performant
foundation in place would risk exactly the kind of unnecessary rewrite
of stable, validated code the Required Working Method warns against.
Tracked as required follow-up work, not silently dropped.

**Python**: `fl_platform.privacy.secure_random` truthfully detects
`secrets`/`os.urandom` availability by performing a real draw (always
succeeds on CPython, but checked rather than assumed) via
`secure_random_available()`, exposes `require_secure_random()` (raises
`SecureRandomUnavailableError` rather than silently downgrading), and
deliberately keeps a *second*, independent function,
`worker_reports_secure_random_support()`, for what
`WorkerPrivacyCapabilities.supports_secure_random` actually advertises —
still `False` today, correctly, since Opacus's own RNG generates
sample-level noise, not this module. `coordinator_client.py`'s
`register_worker` now calls this real function instead of a bare `False`
literal. See [known-limitations.md](known-limitations.md).

## 8. Deferred component index

Everything else this category specifies is **Deferred**, not started
this pass: TLS/mTLS for gRPC (Go↔coordinator, worker↔coordinator);
long-term worker identity (Ed25519 keypairs, certificates, revocation);
signed capability statements; ephemeral session key exchange (X25519);
pairwise mask derivation; private masks; encrypted secret-share
distribution and threshold reconstruction; the full protocol state
machine and transcript integrity; fixed-point tensor encoding for
masked aggregation; secure-aggregation integration with FedAvg-family
algorithms, user-level DP, hybrid DP, and adaptive clipping; Go security
APIs; the web Security Center; new Prometheus metrics/events/audit
records for any of the above; Docker validation of mTLS/secure
aggregation/dropout scenarios; and performance benchmarking. See
[secure-aggregation-report.md](secure-aggregation-report.md) for the
itemized status of every individual completion-gate requirement and a
recommended continuation order.

## 9. Module layout (planned, for when protocol work begins)

```text
cpp/security/            # SecureRandomProvider (implemented), signature
                          # verification, transcript hashing (deferred)
cpp/secure_aggregation/   # protocol state machine, cohort manager,
                          # fixed-point encoder, masked accumulator (deferred)
cpp/identity/             # worker identity registry, certificate handling (deferred)
python/src/fl_platform/privacy/secure_random.py        # implemented
python/src/fl_platform/privacy/budget_enforcement.py    # implemented
python/src/fl_platform/security/                        # existing HMAC-envelope
                                                          # scaffold (unchanged, superseded
                                                          # for identity purposes, see §2)
python/src/fl_platform/secure_aggregation/               # deferred: identity, signing,
                                                          # masking, share handling
```
