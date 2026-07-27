# Secure Aggregation Protocol Foundation and No-Dropout Masked-Sum Core — Completion Report

**Status: the cryptographic and mathematical core is implemented, real,
tested, and cross-language-verified. No live wire protocol exists —
this is a tested library, not a running secure-aggregation RPC path.**
See [secure-aggregation-protocol-foundation.md](secure-aggregation-protocol-foundation.md)
for the Tier 1/Tier 2 scope decision this report evaluates against, and
[known-limitations.md](known-limitations.md)'s "Secure Aggregation
Protocol Foundation and No-Dropout Masked-Sum Core slice" section for
the itemized honest gap list this report summarizes.

Provider name implemented: **`SECAGG_NO_DROPOUT_EXPERIMENTAL`**. Never
`SECURE_AGGREGATION_COMPLETE`. No claim of full secure-aggregation
support anywhere in this slice's code or documentation.

## 1. What was built (Tier 1 — real code, real tests)

| Area | C++ | Python | Cross-language evidence |
|---|---|---|---|
| Cryptographic provider decision | [secure-aggregation-cryptographic-provider.md](secure-aggregation-cryptographic-provider.md) — corrects the stale libsodium claim; OpenSSL EVP selected | PyNaCl (X25519) + `cryptography` (HKDF/ChaCha20) + stdlib `hashlib` (SHA-256) | Existing Ed25519 interop already proven; this slice extends the same provider pair |
| Fixed-point encoding + domain bounds proof | `secure_aggregation_encoding.{hpp,cpp}` | `fixed_point_encoding.py` | `fixtures/secure_aggregation/fixed_point_encoding_golden.json` — 17 hand-derived vectors, checked in both languages |
| Cohort state machine + session config | `secure_aggregation_session.{hpp,cpp}` | `cohort_state_machine.py` | Identical state-transition allow-list, identical field set |
| Pairwise mask sign rule + ring arithmetic | `secure_aggregation_mask.{hpp,cpp}` | `pairwise_mask.py` | Identical cancellation proof (4-participant, independently computed) in both languages |
| X25519 / HKDF-SHA-256 / ChaCha20 / SHA-256 / cohort commitment / session hash | `secure_aggregation_crypto.{hpp,cpp}` (gRPC-gated) | `crypto.py` | `cohort_commitment_golden.json`, `session_configuration_hash_golden.json` — frozen from one reviewed C++ run, matched independently by Python |
| Tensor/weight mask generation | `secure_aggregation_tensor_mask.{hpp,cpp}` (gRPC-gated) | `tensor_mask.py` | `tensor_mask_stream_golden.json` + the capstone integration test (below) |

**The capstone test** (`secure_aggregation_tensor_mask_test.cpp` /
`test_secure_aggregation_tensor_mask.py`, independently implemented in
both languages): constructs a real 4-participant cohort with real
X25519 keypairs, derives all 6 pairwise shared secrets, masks each
participant's real fixed-point-encoded scalar contribution with real
HKDF/ChaCha20-derived masks, and proves —

1. The complete cohort's masked-sum decodes to the exact true aggregate
   (`decode(sum(masked_i for all i)) == sum(true_values)`, to floating-
   point precision).
2. Removing even one participant's masked contribution before summing
   breaks that cancellation completely (`|decoded_partial_sum -
   true_partial_sum| > 1e-3`, not merely "slightly off").

This is the concrete mathematical justification for the mandatory
abort-on-dropout behavior required by the Threshold Secret-Sharing
Blocker in this slice's task specification: a masked-sum protocol with
no reconstruction mechanism has exactly one correct response to a
missing participant, and this test proves that response is necessary,
not just policy.

## 2. What was explicitly not built (Tier 2 — deferred, with reasons)

- No protobuf wire messages, no gRPC RPCs, no coordinator/worker
  handler wiring. `MessageStream::kSecureAggregation` exists in the
  replay-protection enum (schema-only, additive) but no RPC constructs
  a `ReplayCandidate` on it.
- No live FedAvg integration.
- No dropout *detection* logic (deadlines, timeouts) — there is no live
  session for it to watch. The dropout-breaks-cancellation *math* is
  proven (§1 above); the *detection* of a real dropout in a real round
  is not implemented.
- No `EVENT_*`/metric emission call sites, no Go/web observability
  endpoints, no validation-harness scenario group, no artifact-
  sanitation pattern additions, no real multi-worker Docker validation
  of an actual secure-aggregation round.
- No threshold secret sharing, dropout recovery, or partial-cohort
  reconstruction of any kind, per the Threshold Secret-Sharing Blocker
  (unchanged from before this slice — no vetted dependency selected).
- `compute_session_configuration_hash` covers the top-level
  `scale_factor` field but not `fixed_point_profile`'s other sub-fields
  — documented in `session_configuration_hash_golden.json`'s
  `known_limitation` field and in known-limitations.md.

## 3. Test evidence (fresh run, this session)

**C++, local Windows/MSVC** (`cmake --build build/cpp-debug --target
fl_coordinator_tests` + direct run):

```
28/28 test groups passed (fl_coordinator_tests), exit code 0
  ... including [26/28] fixed_point_encoding, [27/28] pairwise_mask,
      [28/28] cohort_state_machine (all new this slice)
```

**C++, Docker/Ubuntu 24.04 (gRPC-gated build — the only environment
that configures `fl_coordinator_grpc_server` and the OpenSSL-linked
targets)**:

```
docker run ... mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04 bash -c '
  apt-get install -y protobuf-compiler protobuf-compiler-grpc libprotobuf-dev libgrpc++-dev pkg-config
  cmake -S cpp -B build/cpp-docker-secagg -DCMAKE_BUILD_TYPE=Release
  cmake --build build/cpp-docker-secagg -j"$(nproc)"
  ctest --test-dir build/cpp-docker-secagg --output-on-failure
'
100% tests passed, 0 tests failed out of 14
  ... including fl_secure_aggregation_crypto_tests (real OpenSSL X25519/
      HKDF/ChaCha20/SHA-256, 2 golden fixtures) and
      fl_secure_aggregation_tensor_mask_tests (the capstone test)
```

Throwaway build directory and image removed after validation
(`rm -rf build/cpp-docker-secagg`, `docker rmi ...`).

**Python** (`pytest python/tests`, deps installed fresh into `.venv`
this session — `pynacl`, `cryptography`, `opacus` were declared in
`requirements.txt` but not yet installed locally; installed via
`pip install -r requirements.txt`):

```
328 passed, 6 skipped, 20 warnings
  ... including 23 (fixed_point_encoding) + 11 (pairwise_mask) +
      9 (cohort_state_machine) + 11 (crypto) + 6 (tensor_mask) = 60
      new tests this slice, all passing, including 5 golden-fixture
      cross-language checks and the independent Python capstone proof
```

Three pre-existing test files were excluded from this run
(`test_worker_entrypoint_wiring.py`, `test_grpc_coordinator_client.py`,
`test_worker_transport.py`) — all three require `grpc` (grpcio), which
is not installed in this local `.venv` and is unrelated to this slice's
changes; this matches this project's established Docker/CI-only
gRPC-build precedent.

**Terminology**: `python scripts/check_project_terminology.py` — pass,
run repeatedly before and after every code/doc change this session.

## 4. Commands not run, and why

- `go test`, `npm run test/build`, full Docker Compose multi-worker
  validation, CI gate wiring, performance benchmarking, artifact
  sanitation pattern additions — none of this slice's Tier 1 work
  touches Go, web, or the live Compose stack, so re-running those
  suites would only re-confirm the absence of regression in code this
  slice did not modify. Not run; not claimed as validated by this
  slice.
- Real 3+-worker Docker validation of an actual secure-aggregation
  round — explicitly Tier 2 (§2 above); there is no live protocol yet
  to validate against real workers.

## 5. Files changed this slice

New: `docs/secure-aggregation-cryptographic-provider.md`,
`docs/secure-aggregation-protocol-foundation.md`,
`docs/secure-aggregation-no-dropout-core-report.md` (this file);
`cpp/coordinator/include/fl_coordinator/secure_aggregation_{encoding,mask,session,crypto,tensor_mask}.hpp`
+ matching `.cpp`; `cpp/coordinator/tests/{fixed_point_encoding,pairwise_mask,cohort_state_machine,secure_aggregation_crypto,secure_aggregation_tensor_mask}_test.cpp`;
`python/src/fl_platform/secure_aggregation/` (new package: `__init__.py`,
`fixed_point_encoding.py`, `pairwise_mask.py`, `cohort_state_machine.py`,
`crypto.py`, `tensor_mask.py`); `python/tests/test_secure_aggregation_*.py`
(5 files); `fixtures/secure_aggregation/` (4 golden-fixture JSON files).

Modified: `cpp/CMakeLists.txt` (new source files + 2 new standalone
gRPC-gated test targets), `cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp`
+ `.cpp` (added `MessageStream::kSecureAggregation`, schema-only),
`cpp/coordinator/tests/test_main.cpp` (registers the 3 new
non-gRPC-gated test groups), `docs/cryptographic-primitives.md`
(superseded-notice banner), `docs/known-limitations.md` (new section +
one corrected libsodium reference), `plan.md` §7 (status update banner).

Nothing committed, pushed, tagged, or opened as a pull request, per
this slice's explicit instruction.
