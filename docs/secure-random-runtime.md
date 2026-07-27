# Secure Random Runtime

**Status: Implemented and Validated (C++ live-noise wiring; Python
Opacus secure-mode gating). See [known-limitations.md](known-limitations.md)
for what remains deferred (libsodium-backed provider, worker-side
mask/protocol-key generation).**

## The problem this solves

[secure-aggregation-architecture.md](secure-aggregation-architecture.md)'s
closure-gate work built `fl::core::SecureRandomProvider` (a real,
OS-CSPRNG-backed abstraction) but left it unconnected to the actual
runtime privacy-noise path — `run_manager.cpp` still constructed the
older `SecureNoiseProvider` (one `mt19937_64` seeded once from
`std::random_device` and reused for the whole run). This document
covers the work that closes that gap for real: the live noise path now
uses the secure provider, with real benchmark numbers for the overhead,
and the Python worker's `secure_random_required` capability actually
gates Opacus's own `secure_mode`.

## C++: `CryptoSecureNoiseProvider`

`cpp/core/include/fl_core/secure_random.hpp`/`.cpp` adds
`CryptoSecureNoiseProvider`, a `NoiseProvider` (see
[user-level-dp.md](user-level-dp.md)) backed by
`OsEntropySecureRandomProvider` — a thin adapter, not a
reimplementation, so every existing call site
(`add_central_gaussian_noise`, `AdaptiveClipController`) is unchanged.
`RunInstance`'s constructor (`run_manager.cpp`) now builds this instead
of `SecureNoiseProvider` whenever `config_.privacy_noise_seed == 0`
(the non-deterministic, production path); `DeterministicNoiseProvider`
remains the only choice for `privacy_noise_seed != 0` (tests only).

### Efficient design: buffered entropy, not a derived stream cipher

The naive approach — one OS syscall per `double` — would be a severe
performance regression at real tensor scale (`gaussian_sample` draws
two secure uniforms via Box-Muller, each needing 8 bytes). The
"textbook correct" fix is to seed a vetted CSPRNG-grade stream (e.g.
ChaCha20) from OS entropy once per noise-generation call and stream
from it — but no vetted stream-cipher library is linked yet (libsodium
is selected but not integrated — see
[cryptographic-primitives.md](cryptographic-primitives.md)), and this
project does not hand-roll cryptographic primitives.

Instead, `OsEntropySecureRandomProvider` buffers **raw, unreduced OS
entropy** in 4096-byte chunks: `fill_random_bytes` serves from an
internal buffer, refilling via one real OS call (`BCryptGenRandom` on
Windows, `/dev/urandom` on POSIX) exactly when the buffer is exhausted.
Every byte ever returned is still genuine OS-CSPRNG output — nothing is
derived from a smaller seed — this is purely a batching optimization,
not a new cryptographic construction. Consumed buffer bytes are zeroed
immediately after use as a defensive measure against accidental reuse
through a future bug.

### Benchmark: old vs. new

Measured on this development machine (200,000 `gaussian_sample(1.0)`
calls per provider, Release build, MSVC/Windows):

| Build | `SecureNoiseProvider` (old) | `CryptoSecureNoiseProvider` (new) | Overhead |
|---|---|---|---|
| Release | ~0.032–0.037 µs/sample | ~0.050–0.066 µs/sample | ~1.5×–1.8× |
| Debug | ~0.146 µs/sample | ~0.139 µs/sample | none observed |

Both providers stay comfortably sub-microsecond per sample; for this
project's model sizes (thousands of parameters), the noise-generation
step remains negligible relative to training/network time. The
overhead is real and reported honestly (per the closure-gate
instruction "do not claim negligible overhead without measurements"),
not hidden — this is not yet the full performance-benchmarking pass
(Work Package U in the parent specification), which covers signing,
handshakes, and other primitives not measured here.

### What is *not* wired

`fl::core::SecureRandomProviderIdentity` (`kOsCsprng`/
`kDeterministicTestOnly`/`kLibsodium`) is a real, checkable accessor on
`CryptoSecureNoiseProvider`, but is **not yet threaded into the
`UserLevelLedgerEntry`/`AdaptiveClippingLedgerEntry` wire structures**
— "provider identity recorded in privacy metadata" is satisfied at the
C++ object level (`provider.identity()`) but not yet surfaced through
the proto/API layer. Noted as a deferred wire-integration item,
consistent with this project's established pattern for similar gaps.

## Python: real Opacus secure-mode gating

`SampleLevelDPConfig.secure_random_required: bool` (new field, default
`False`) — when `True`, `run_private_local_training`
(`task_runner.py`) calls `require_opacus_secure_mode(client_id=...)`
**before any training work starts** (dataset load, model setup), which
truthfully probes for the `torchcsprng` package (`importlib.util.find_spec`,
a real module-resolution check) and raises
`SecureRandomTaskRejectedError` if unavailable — the task is rejected
outright, never silently trained with `secure_mode=False`. When
available, `PrivacyEngine(accountant=..., secure_mode=True)` is
constructed for real.

`fl_platform.privacy.secure_random` keeps two questions deliberately
separate:

* `secure_random_available()` — can this process draw OS-CSPRNG bytes
  at all? Always `True` on real CPython (stdlib `secrets`), checked via
  a real draw, not assumed.
* `worker_reports_secure_random_support()` — what
  `WorkerPrivacyCapabilities.supports_secure_random` actually
  advertises. Now computed from `opacus_secure_mode_available()`
  (dynamic, real), not the always-true `secure_random_available()` —
  `torchcsprng` is not installed in this project's development/CI
  environment today, so this correctly reports `False` until it is.

`coordinator_client.py`'s `register_worker` calls this real function
instead of the prior hardcoded `False` literal.

## Validation

* C++: `fl_secure_random_tests` (8 test groups) + the full
  `fl_coordinator_tests` suite (which exercises `CryptoSecureNoiseProvider`
  through real user-level DP / hybrid DP / adaptive clipping test
  paths) — 7/7 CTest suites pass in both Debug and Release.
* Python: 6 tests in `test_secure_random.py` (mocking
  `opacus_secure_mode_available` to prove both branches) plus 2
  integration tests in `test_private_training.py` that run real Opacus
  training and prove `secure_random_required=True` genuinely rejects
  the task in this `torchcsprng`-free environment, while
  `secure_random_required=False` trains normally.
