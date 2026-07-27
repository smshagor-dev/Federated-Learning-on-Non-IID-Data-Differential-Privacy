# Cryptographic Dependency Selection

**SUPERSEDED (partially) by
[secure-aggregation-cryptographic-provider.md](secure-aggregation-cryptographic-provider.md):**
this document's C++ selection below (libsodium) was written before any
C++ code actually used it, and was never integrated — real, working,
tested, live-validated C++ crypto code
(`coordinator_signing_identity.cpp`, and now
`secure_aggregation_crypto.cpp`) uses **OpenSSL EVP** instead, since
`gRPC::grpc++` already links OpenSSL transitively for its own TLS
implementation, and a second crypto dependency was never actually
added. The Python selection below (PyNaCl + `cryptography`) remains
accurate and is unchanged. See
secure-aggregation-cryptographic-provider.md §1–§3 for the full
repository-audit finding and corrected decision table. This document's
§4 (threshold secret sharing has no selection) still stands and is not
superseded.

**Status: dependency selection is complete and documented below, verified
against current upstream sources (search performed while writing this
document, not recalled from training data). Actual linkage/installation
into the build is deferred — see
[secure-aggregation-architecture.md](secure-aggregation-architecture.md).
One dependency category (threshold secret sharing) has **no** selection
yet; see [§4](#4-threshold-secret-sharing-no-selection-yet--documented-blocker).**

This project does not implement cryptographic primitives by hand. Every
primitive below is provided by an existing, widely-used library.

## 1. C++

**libsodium** — selected for Ed25519 signatures, X25519 key agreement,
HKDF-SHA-256, XChaCha20-Poly1305 AEAD, and OS-backed cryptographic random
bytes (`randombytes_buf`).

* **License**: ISC (permissive, compatible with this project's existing
  dependency set).
* **Maintenance**: actively maintained by its original author
  (`jedisct1`); latest stable release as of this writing is the 1.0.21
  point release, with ongoing point/stable releases and recent platform
  compatibility work. Widely embedded (it is the reference
  implementation the "libsodium" ecosystem, including PyNaCl below, is
  built on).
* **Platform support**: builds on Windows, Linux, and macOS; ships
  prebuilt packages via most system package managers and via source
  build, matching this project's existing pattern of `find_package(...
  QUIET)` + CI/Docker-built targets for dependencies not available on
  this Windows development machine (the same pattern already used for
  gRPC/Protobuf).
* **Integration plan**: `find_package(unofficial-sodium QUIET)` or a
  `pkg_check_modules`-based lookup, gating a new `fl_security` C++
  target the same way `fl_coordinator_grpc_server` is gated on
  `Protobuf_FOUND AND gRPC_FOUND`. Built for real in
  `infra/docker/cpp-coordinator.Dockerfile` (apt: `libsodium-dev`) and
  in CI (`ubuntu-latest`).

Sources: [libsodium GitHub](https://github.com/jedisct1/libsodium),
[libsodium LICENSE](https://github.com/jedisct1/libsodium/blob/master/LICENSE),
[libsodium releases](https://github.com/jedisct1/libsodium/releases).

OpenSSL is explicitly **not** selected as the primary C++ dependency —
libsodium's API surface is a closer match to this category's required
primitive list (Ed25519/X25519/XChaCha20-Poly1305/HKDF as first-class,
misuse-resistant functions) and avoids OpenSSL's larger attack surface
and more error-prone low-level API for the specific primitives needed
here. OpenSSL remains available as a fallback only where TLS/mTLS
specifically requires it (gRPC's own TLS credential machinery is
typically backed by BoringSSL/OpenSSL internally, which is a transport
concern, not this category's own primitive selection — see
[mtls.md](mtls.md) once written).

## 2. Python

**PyNaCl** — the libsodium binding, selected for parity with the C++
selection above (same underlying primitives, same security properties,
avoids a second independent crypto implementation to keep in sync).

* **License**: Apache-2.0.
* **Maintenance**: maintained by the Python Cryptographic Authority
  (`pyca`), the same organization that maintains the `cryptography`
  package below; latest release as of this writing is 1.6.2 on PyPI.
  Supports Python 3.8+.

Source: [PyNaCl GitHub](https://github.com/pyca/pynacl),
[PyNaCl on PyPI](https://pypi.org/project/PyNaCl/).

**`cryptography`** (pyca) — selected as a complementary dependency for
X.509 certificate handling (mTLS certificate parsing/validation,
expiry checks) and any primitive PyNaCl does not expose directly.

* **License**: dual Apache-2.0 / BSD.
* **Maintenance**: maintained by `pyca`; latest release as of this
  writing is 49.0.0, with a regular multi-release-per-year cadence
  through this year.

Source: [cryptography GitHub](https://github.com/pyca/cryptography),
[cryptography.io](https://cryptography.io/en/stable/).

Both packages will be added to `python/pyproject.toml`'s
`[project.optional-dependencies]` (or a new dedicated group) and to
`requirements.txt` (used by CI and the worker Docker image, matching
the pattern already established for `opacus`/`prometheus_client` in the
Privacy Engineering category).

## 3. Required primitives and their source

| Primitive | Provided by |
|---|---|
| Ed25519 signatures | libsodium (`crypto_sign_*`) / PyNaCl (`nacl.signing`) |
| X25519 key agreement | libsodium (`crypto_kx_*`/`crypto_scalarmult`) / PyNaCl (`nacl.public`) |
| HKDF-SHA-256 | libsodium (`crypto_kdf_hkdf_*`, added in libsodium 1.0.18+) / PyNaCl (via `cryptography`'s `hashes`/`hkdf` module, since PyNaCl does not itself expose HKDF) |
| XChaCha20-Poly1305 AEAD | libsodium (`crypto_aead_xchacha20poly1305_ietf_*`) / PyNaCl (`nacl.secret.Aead` / `nacl.bindings`) |
| ChaCha20-based deterministic mask generation | libsodium's `crypto_stream_chacha20` family, keyed by the session-bound shared secret (see [pairwise-masking.md](pairwise-masking.md) once written) |
| Cryptographic random bytes | libsodium `randombytes_buf` (C++) / Python `secrets`/`os.urandom` (stdlib, no third-party dependency needed — see the secure-random provider work in this pass) |
| Secure hashing | libsodium `crypto_generichash` (BLAKE2b) or SHA-256 via the same library, for transcript hashing |
| Constant-time comparisons | libsodium `sodium_memcmp` (C++) / `hmac.compare_digest` (Python stdlib, already used by the existing HMAC envelope scaffold) |
| Threshold secret sharing | **No selection yet — see §4.** |

None of Ed25519, X25519, AEAD, HKDF, PRG internals, hash functions, or
random-number generators will be hand-implemented, per this category's
explicit prohibition — every one of the primitives above is a library
call, not new cryptographic code.

## 4. Threshold secret sharing — no selection yet, documented blocker

The category's own instructions require: "Select a maintained, reviewed
dependency where practical... If no acceptable dependency is available,
stop and report the blocker instead of writing unreviewed cryptography."
That is exactly the situation found here.

Candidates investigated:

* **`dsprenkels/sss`** (C, GF(256)-based Shamir secret sharing,
  designed with side-channel resistance and an AEAD wrapper in mind).
  The repository itself states it has been superseded by a fork,
  `BlockchainCommons/sss`; no confirmed independent security audit was
  found for either the original or the fork.
* **`trezor/python-shamir-mnemonic`** (Python, reference implementation
  of SLIP-0039). Its own documentation states it exists "to verify
  correctness of other implementations," should **not** be used for
  handling sensitive secrets, and its "calculations are most likely
  trivially vulnerable to side-channel attacks" — explicitly
  unsuitable for this project's purpose despite being maintained by a
  reputable organization for a different purpose (mnemonic backup
  verification, not runtime secret protection).
* No canonical, independently-audited, actively-maintained C++ or
  Python threshold secret-sharing library comparable in trust standing
  to libsodium/PyNaCl/`cryptography` was found in this search.

**Decision: this is a real blocker, reported rather than papered over.**
Encrypted secret-share distribution and dropout recovery (the protocol
stages that depend on threshold secret sharing) cannot proceed with a
selected dependency until one of the following happens:

1. A more thorough, dedicated review turns up an acceptable option not
   surfaced by this pass's search (candidates worth a deeper look next:
   whether a stable, audited implementation has shipped inside a widely
   trusted project this project could vendor a narrow slice of, or
   whether libsodium's own roadmap adds secret sharing — it does not
   today), or
2. The reconstruction-threshold requirement is satisfied by a different
   mechanism this category's instructions already permit examining
   (e.g., a straightforward XOR-based n-of-n scheme is not equivalent to
   general k-of-n Shamir sharing and does not meet the "share
   reconstruction threshold" cohort-model requirement, so it is not a
   drop-in substitute — noted here, not adopted), or
3. An operator/maintainer explicitly accepts a specific candidate after
   reviewing its audit status themselves.

Until one of those happens, **no share-encryption/reconstruction code is
written**, per the explicit instruction not to write unreviewed
cryptography. This is recorded as an open item in
[secure-aggregation-report.md](secure-aggregation-report.md)'s remaining
trust assumptions and known limitations, not silently deferred.

## 5. What this pass actually integrates

Given §4's blocker, this pass integrates **no new external cryptographic
dependency into the build** yet — see
[secure-aggregation-architecture.md](secure-aggregation-architecture.md)'s
closure-gate sections for what *is* implemented (worker-side sample
budget enforcement, and an OS-entropy-backed — not yet libsodium-backed
— secure random provider using each language's own standard-library
CSPRNG access, which needs no new dependency). Adding libsodium/PyNaCl
to the build is the first concrete action of the next continuation pass,
once transport/identity work begins in earnest.
