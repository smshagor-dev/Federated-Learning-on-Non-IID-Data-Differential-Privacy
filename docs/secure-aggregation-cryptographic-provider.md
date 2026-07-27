# Secure Aggregation Cryptographic Provider Decision

**Status: GO. A vetted, cross-language-compatible provider combination
is selected below and verified buildable on the actual target
environment.** Secure Aggregation Protocol Foundation and No-Dropout
Masked-Sum Core slice, Work Package A. This document supersedes
[cryptographic-primitives.md](cryptographic-primitives.md)'s C++
selection for the specific primitives this slice needs (X25519, HKDF-
SHA-256, ChaCha20 keystream) — see §6 for why, and why the change is
not a re-litigation of Ed25519 (unchanged, working, not touched).

## 1. Repository audit finding that drives this decision

`cryptographic-primitives.md` (an earlier design-record pass, before
any of this category's identity/signing/mTLS work existed as real
code) selected **libsodium** for C++. That selection was never
integrated — direct inspection of the current, real, working code
shows the C++ side actually uses **OpenSSL EVP** for Ed25519
(`cpp/coordinator/src/coordinator_signing_identity.cpp`:
`EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, ...)`,
`EVP_DigestSign`/`EVP_DigestVerify`), not libsodium. This is a real,
working, tested, live-validated production path (coordinator signing
identity, task signing, capability/envelope verification — all of this
category's already-`RESEARCH_SECURITY_READY`-classified security
foundation). The reason, recorded in a code comment
(`cpp/CMakeLists.txt:185-191`) but never reflected in
`cryptographic-primitives.md`: `gRPC::grpc++` already links OpenSSL
transitively for its own TLS implementation, so requiring
`find_package(OpenSSL REQUIRED)` for Ed25519 inside the same
gRPC-gated CMake block introduces **no new dependency** — libsodium
would be a second, redundant crypto library alongside one already
linked and already proven working in this exact build.

Python's side already matches the original document: **PyNaCl**
(`nacl.signing`) for Ed25519, **`cryptography`** (pyca) already a
declared dependency (`python/pyproject.toml`'s `security` extra) and
already imported (`capability_statement.py`).

## 2. Decision

| Primitive | C++ | Python |
|---|---|---|
| Ed25519 signatures | OpenSSL EVP (`EVP_PKEY_ED25519`) — **unchanged, already live** | PyNaCl (`nacl.signing`) — **unchanged, already live** |
| X25519 key agreement | OpenSSL EVP (`EVP_PKEY_X25519`) | `cryptography` (`hazmat.primitives.asymmetric.x25519`) |
| HKDF-SHA-256 | OpenSSL EVP (`EVP_KDF` HKDF, or `EVP_PKEY_CTX` HKDF derive on the OpenSSL version this project's `EVP_PKEY_ED25519` usage already implies) | `cryptography` (`hazmat.primitives.kdf.hkdf.HKDF`) |
| Deterministic pseudorandom mask stream | OpenSSL EVP (`EVP_chacha20`, IETF ChaCha20, keystream via encrypting an all-zero buffer) | `cryptography` (`hazmat.primitives.ciphers.algorithms.ChaCha20`, same construction) |
| SHA-256 (cohort commitment, canonical payload hashes) | OpenSSL EVP (`EVP_sha256`) — **same function already used for `payload_hash`/task-signing hashes** | Python stdlib `hashlib.sha256` — **already used project-wide for `sha256_hex`** |
| OS-backed CSPRNG (ephemeral X25519 keypair generation) | `fl::core::OsEntropySecureRandomProvider` — **unchanged, already live and tested** (`cpp/core/include/fl_core/secure_random.hpp`; `BCryptGenRandom`/`/dev/urandom`) | Python `secrets`/`os.urandom` via `fl_platform.privacy.secure_random` — **unchanged, already live** |
| Constant-time comparison | OpenSSL `CRYPTO_memcmp` | Python stdlib `hmac.compare_digest` — **already used by the existing envelope-verification code** |

No primitive in this table is hand-implemented. Every one is a single
call into OpenSSL (C++) or `cryptography`/PyNaCl/stdlib (Python), both
already real, load-bearing dependencies in this codebase before this
slice.

## 3. Why not libsodium (reversing the earlier document)

- It would be a **second** crypto library alongside OpenSSL, which is
  already required, linked, and the actual source of every Ed25519
  operation this category's already-shipped, already-`RESEARCH_SECURITY_READY`
  code performs. Two crypto libraries for overlapping primitive sets is
  more attack surface and more to keep in sync, not less.
- OpenSSL 3.0 (the version Ubuntu 24.04 ships — the actual base image
  of `infra/docker/cpp-coordinator.Dockerfile`, confirmed by direct
  read) has first-class, misuse-resistant EVP support for every
  primitive this slice needs: `EVP_PKEY_X25519` (added in OpenSSL
  1.1.0), `EVP_KDF` HKDF (OpenSSL 3.0's modern KDF API; OpenSSL 1.1.0+
  also supports HKDF via `EVP_PKEY_CTX_set_hkdf_*` on an
  `EVP_PKEY_HKDF` context, so the primitive is available regardless of
  which sub-API is used), and `EVP_chacha20` (present since OpenSSL
  1.1.0). Nothing this slice needs requires a newer OpenSSL than what
  the existing Ed25519 code already assumes.
- Python already uses `cryptography` (pyca), the same organization that
  maintains PyNaCl, for X.509/HKDF-adjacent work — extending its use to
  X25519/HKDF/ChaCha20 keeps one fewer moving part than adding a
  second library there too. PyNaCl remains the Ed25519 provider,
  unchanged.

## 4. Go/no-go gate

**GO.** Every primitive in §2 is available from an already-integrated,
already-linked, already-tested library in both languages, on the
actual target build environment (Ubuntu 24.04 via
`infra/docker/cpp-coordinator.Dockerfile`, the only environment that
can build the gRPC-gated C++ coordinator code — this Windows
development machine cannot, per the pre-existing, documented,
unchanged constraint recorded in `docs/coordinator-runtime.md`/
`docs/known-limitations.md`). Cross-language byte-identical fixture
verification is required before any primitive is trusted (see §8) —
this document records the *selection*; §8's fixtures are the actual
proof, produced and checked as part of this slice's implementation
work, not asserted here in advance.

## 5. License, maintenance, platform support

| Item | OpenSSL (C++) | `cryptography` (Python) | PyNaCl (Python, unchanged) |
|---|---|---|---|
| License | Apache-2.0 (OpenSSL 3.x) | Apache-2.0 / BSD dual | Apache-2.0 |
| Maintainer | OpenSSL Software Foundation | pyca | pyca |
| Version in this build | 3.0.x (Ubuntu 24.04 `libssl-dev`/`libgrpc++-dev` transitive dependency — no explicit pin exists in this repository; already the case for the pre-existing Ed25519 code) | `>=42,<50` (`python/pyproject.toml`, unchanged) | `>=1.5,<2` (unchanged) |
| Windows support | Present via vcpkg/system OpenSSL, but **irrelevant to this build**: the gRPC-gated coordinator target is skipped entirely on this Windows dev machine (`find_package(gRPC QUIET)` fails to find it) — matches the existing, unchanged, documented pattern for every other gRPC-gated file in this repository | Yes (wheels ship prebuilt binaries) | Yes |
| Linux support | Yes — the only platform that actually builds this code path today | Yes | Yes |
| Docker support | Yes — `infra/docker/cpp-coordinator.Dockerfile`'s base image already provides it | Yes | Yes |
| CI support | Yes — `.github/workflows/ci.yml`'s `cpp-grpc` job already builds on `ubuntu-latest` with `libgrpc++-dev` (which pulls OpenSSL) | Yes — `ci.yml`'s `python` job already installs `python[dev,security]` | Yes |
| FIPS implications | OpenSSL 3.x has an optional FIPS provider; this project does not enable or claim FIPS-mode operation — noted, not pursued, matching this category's explicit non-goal of independent cryptographic certification | N/A (not FIPS-validated as used here) | N/A |

## 6. Nonce, counter, endianness, key-length, output-length, error, cleanup conventions

These are fixed here so both languages implement byte-identical
behavior — verified by the golden fixtures in §8, not asserted from
memory of either library's documentation.

- **X25519 keys**: 32-byte raw public key, 32-byte raw private
  (scalar) key. OpenSSL: `EVP_PKEY_get_raw_public_key`/
  `EVP_PKEY_get_raw_private_key` (same raw-byte convention already used
  for this project's Ed25519 keys — see
  `coordinator_signing_identity.cpp`). Python: `cryptography`'s
  `X25519PublicKey.public_bytes(Encoding.Raw, PublicFormat.Raw)`/
  `X25519PrivateKey.private_bytes(Encoding.Raw, PrivateFormat.Raw,
  NoEncryption())`. All-zero public key is explicitly rejected (a
  known X25519 low-order-point degenerate case) — both languages check
  this before deriving a shared secret, never delegate the check to
  the library silently succeeding.
- **X25519 shared secret**: 32 raw bytes,
  `EVP_PKEY_derive`/`private_key.exchange(peer_public_key)`. The raw
  X25519 output is **never used directly as a mask key** — it is
  always passed through HKDF first (see Work Package Q's domain
  separation), so a raw low-entropy or structured X25519 output can
  never leak directly into mask generation.
- **HKDF-SHA-256**: `extract` then `expand`, standard RFC 5869
  two-step construction. `salt` is a fixed, empty-or-documented value
  per derivation context (never secret); `info` carries the full
  domain-separation label (protocol name/version/purpose/session/
  round/model-version/cohort-commitment/participant-ordering/tensor-
  name/index/chunk-index, per Work Package Q). Output length is
  fixed per purpose (32 bytes for a ChaCha20 key, unless a specific
  derivation documents otherwise). OpenSSL: `EVP_PKEY_CTX` configured
  with `EVP_PKEY_HKDF`, `EVP_PKEY_CTX_set_hkdf_md(SHA256)`,
  `_set1_hkdf_salt`, `_set1_hkdf_key`, `_add1_hkdf_info`,
  `EVP_PKEY_derive`. Python: `cryptography.hazmat.primitives.kdf.hkdf.HKDF(
  algorithm=SHA256(), length=N, salt=..., info=...)`.
- **ChaCha20 keystream**: IETF variant (RFC 8439 framing: 32-byte key,
  12-byte nonce, 32-bit little-endian block counter, distinct from the
  original Bernstein 64-bit-counter/8-byte-nonce variant) — this is
  the variant both OpenSSL's `EVP_chacha20` and Python
  `cryptography`'s `algorithms.ChaCha20` implement, so selecting it
  (rather than the original variant) is what makes cross-language
  parity achievable without hand-rolling either. Keystream bytes are
  produced by encrypting an all-zero plaintext buffer of the requested
  length with counter starting at 0 unless a derivation context
  documents a different starting counter (e.g. resuming a chunked
  stream at a chunk boundary — see Work Package T). Nonce is
  HKDF-derived per purpose (tensor/weight/chunk), never reused across
  purposes, sessions, or participants pairs, per Work Package Q/S.
  Maximum stream length per (key, nonce) pair is bounded by the
  32-bit counter: `2^32 * 64` bytes (~256 GiB) — far beyond any tensor
  this project handles; still checked, not assumed, and rejected with
  a typed error rather than silently wrapping the counter.
- **Error behavior**: every wrapper function returns a typed error
  (C++: a `Result`-style return or thrown typed exception, never a raw
  boolean/`-1`; Python: a typed exception subclassing a common
  `SecureAggregationCryptoError`) — never a silent zero-filled buffer,
  never a silent fallback to a weaker primitive. Matches this
  project's existing `SecureRandomUnavailableError` "fail closed, never
  silently downgrade" convention.
- **Cleanup behavior**: raw private key material (X25519 private
  scalars, HKDF pseudorandom keys, ChaCha20 keys) is zeroed after use
  where the language allows it — C++: `OPENSSL_cleanse` on any raw
  buffer holding key material before it goes out of scope; Python:
  best-effort only (CPython does not guarantee secure erasure of
  immutable `bytes` objects — this is stated honestly, not claimed as
  a guarantee it cannot make). Neither language persists this key
  material to disk or logs it — enforced by the artifact-sanitation
  patterns added in this slice (see
  [security-ci.md](security-ci.md)).

## 7. Cross-language semantic compatibility

Ed25519, X25519, HKDF-SHA-256, ChaCha20, and SHA-256 are all
standardized (RFC 8032, RFC 7748, RFC 5869, RFC 8439, FIPS 180-4)
byte-for-byte-defined algorithms — the wire format and output of a
correct implementation is identical regardless of which library
produced it. This is already proven in this codebase today: the C++
coordinator (OpenSSL) signs Ed25519 tasks that the Python worker
(PyNaCl) verifies successfully, live, in Docker, every time the
runtime-validation harness runs (see
`security-runtime-validation.md`). The same interoperability applies
to X25519/HKDF/ChaCha20 selected here, and is *proven*, not merely
argued, by the golden fixtures below.

## 8. Golden fixture requirement (byte-identical, both languages)

Per this slice's own requirement ("if no provider combination can
produce byte-identical reviewed vectors... stop"), the following fixed
fixtures are required before any protocol code trusts these
primitives, and are implemented as part of this slice (see
`cpp/coordinator/tests/secure_aggregation_crypto_test.cpp` and
`python/tests/test_secure_aggregation_crypto.py`, both consuming the
same fixture values from `docs/secure-aggregation-cryptographic-provider.md`'s
own appendix / a shared fixture data file — never computed by the
implementation under test and then compared to itself):

- A fixed X25519 keypair (both parties) → the same 32-byte shared
  secret in both languages.
- A fixed HKDF `(ikm, salt, info, length)` tuple → the same output
  bytes in both languages.
- A fixed ChaCha20 `(key, nonce, counter, length)` tuple → the same
  keystream bytes in both languages.
- A fixed SHA-256 input → the same digest in both languages (already
  implicitly proven by every existing cross-language payload-hash
  test in this repository, re-stated here as an explicit fixture for
  this slice's own primitives).

## 9. What this document does not decide

- Threshold secret sharing remains **not selected** — unchanged from
  `cryptographic-primitives.md` §4, and out of scope for this slice by
  explicit instruction (no dropout recovery, no Shamir sharing, no
  reconstruction).
- This document does not select a provider for anything beyond the
  primitive list in §2 — the protocol-level design (session state
  machine, cohort roster, masked-update wire format) is recorded in
  [secure-aggregation-protocol-foundation.md](secure-aggregation-protocol-foundation.md).
