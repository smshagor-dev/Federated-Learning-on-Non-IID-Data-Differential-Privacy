# Payload Hashing

**Status: Implemented and Validated for the Heartbeat Hash, the Client
Result Hash, the Sample Privacy Record Hash, and the Key-Rotation
Request Hash**, including real per-tensor checksum verification and
real cross-language golden fixtures for both the privacy record hash
and the rotation-request hash (see
[signed-privacy-records.md](signed-privacy-records.md) and
[key-rotation.md](key-rotation.md)).

## The general rule

Every signed message type gets its own per-type function that produces
a canonical JSON string (`json.dumps(..., sort_keys=True,
separators=(",", ":"), ensure_ascii=True)` in Python;
`canonical_capability_payload_json`/`canonical_envelope_metadata_json`-style
hand-written encoding in C++ — see
[canonical-security-serialization.md](canonical-security-serialization.md))
over exactly the fields that type's signature should bind to.
`payload_hash = SHA-256(that string)`. Never include the raw tensor
values twice: a hash function binds to an existing checksum/manifest
field (e.g. `TensorManifest.checksum`, already computed by whoever
built the tensor), never to the tensor's raw float array.

## Heartbeat Hash (implemented)

`heartbeat_payload_hash_input` (`cpp/coordinator/src/signed_envelope_verifier.cpp`;
mirrored in Python by every live-test script's identical
`heartbeat_payload_hash_input` function) binds to:

```json
{"current_task_id":"...","status":<int>,"timestamp":<issued_at>,"worker_id":"..."}
```

That is: `worker_id`, `status`, `current_task_id` — every field
`WorkerHeartbeatRequest` actually carries on the wire today — plus the
envelope's own `issued_at` as the heartbeat's timestamp.

**Deliberately excludes** capacity metadata, `software_version`, and
`build_id` (all three named in the parent specification's Heartbeat
Hash field list): none of them are part of `WorkerHeartbeatRequest`'s
current wire format. Adding them would mean new proto fields nothing
else reads yet, purely to satisfy a hash-binding requirement — a
premature abstraction this pass declined to add. `software_version`/
`build_id` are already asserted once, authoritatively, in the signed
capability statement at registration time
([signed-capabilities.md](signed-capabilities.md)); re-asserting them
identically on every heartbeat would not add a real guarantee beyond
what registration-time signing already provides, since the coordinator
already has no way to detect a worker's software silently changing
mid-session without a *new* signed statement asserting the change
either way. If a future pass wants per-heartbeat capacity reporting,
the fields must be added to `WorkerHeartbeatRequest` first and the hash
function extended to bind to them explicitly — not silently assumed.

Verified byte-for-byte via `signed_envelope_verifier_test.cpp` and, more
importantly, via the live end-to-end test: a real PyNaCl signature
computed over Python's independently-implemented version of this exact
hash function was accepted by the C++ verifier's independently-implemented
version — if the two disagreed on a single byte, the SHA-256 comparison
inside `verify_signed_envelope` would have failed before the signature
was even checked.

## Client Result Hash (implemented and live-validated)

`client_result_payload_hash_input` (`signed_envelope_verifier.cpp`
C++; `signed_envelope.py` Python — cross-language parity proven live,
not just by a static vector, since a real PyNaCl signature computed
over Python's independently-implemented canonical bytes was accepted
by the C++ verifier's independently-implemented recomputation — see
[signed-client-results.md](signed-client-results.md)) binds to:
`schema_version`, `run_id`, `round_id`, `task_id`, `client_id`,
`worker_id`, `model_version`, `algorithm`, `sample_count`, `step_count`,
`update_norm`, `completion_timestamp`, `nonce`, a canonically-sorted
(by name) tensor manifest (`name`/`shape`/`dtype`/`byte_length`/
`checksum` per tensor), a canonically-sorted (by name) training-metrics
list, and nested `personalization_metrics`/`privacy_record` objects
(each `{}` when absent — the canonical empty representation).
`privacy_record` additionally carries a `privacy_record_payload_hash`
key (Privacy Record Authenticity slice) — the accompanying signed
`SignedSamplePrivacyRecord` envelope's own `payload_hash`, or `""` when
absent — binding the outer signature to the independent privacy-record
signature as a second, redundant layer. See
[signed-privacy-records.md](signed-privacy-records.md) for the full
design.

**Real per-tensor checksum verification, not just checksum-field
pass-through**: `tensor_checksum_matches` recomputes the SHA-256 over
each tensor's actual `values` (little-endian float64, matching what
`fl_platform.worker.coordinator_client._tensor_manifests_from_dict`
now really computes — previously this field was always empty) and
rejects if it doesn't match the claimed `checksum` — see
[signed-client-results.md](signed-client-results.md) for why this
matters (without it, a signature would only ever have covered the
checksum *string*, never verified it against the real floats).

Deviations from the parent specification's field list, stated
honestly: no "update artifact reference" field exists on the wire
(`fl.worker.v1.ClientResult` transmits tensor values inline, never via
an artifact-store reference — see
[create-run-wire-mapping.md](create-run-wire-mapping.md)'s "tensor
transport" section), so nothing is bound to it. "Privacy metadata hash"/
"personalization metadata hash" are implemented as nested canonical
*objects* embedded directly in the outer payload (hashed once, as part
of the whole), not as separately pre-computed sub-hash strings —
functionally equivalent for tamper-detection purposes (any change to
either nested structure still changes the final SHA-256), simpler to
implement and audit, and avoids an unnecessary extra layer of
indirection. "Completion status" has no dedicated field on the wire;
`completion_timestamp` is bound instead (the closest field that
actually exists).

## Sample Privacy Record Hash (implemented and live-validated)

`sample_privacy_record_payload_hash_input` (`signed_envelope_verifier.cpp`
C++; `signed_envelope.py` Python) binds to all 27 fields of
`fl.privacy.v1.SignedSamplePrivacyRecord`: `schema_version`, `worker_id`,
`run_id`, `round_id`, `task_id`, `client_id`, `model_version`,
`algorithm`, `privacy_mode`, `accountant_type`, `accountant_step`,
`epsilon`, `delta`, `noise_multiplier`, `max_grad_norm`, `sample_rate`,
`expected_batch_size`, `local_epochs`, `configuration_hash`,
`accountant_state_hash`, `budget_target_epsilon`,
`budget_target_delta`, `budget_policy`, `budget_decision`,
`secure_random_required`, `secure_random_available`,
`secure_random_provider`, in alphabetical key order.

Rejects (before hashing) NaN/infinite or negative values for
`epsilon`/`delta`/`noise_multiplier`/`max_grad_norm`/`sample_rate`/
`budget_target_epsilon`/`budget_target_delta` — the same class of
validation `client_result_payload_hash_input` already applies.

**Proven cross-language via a real, reviewed golden fixture, not just
live signature acceptance**: the identical canonical JSON string,
independently generated by Python for a fixed logical record, is
hardcoded as `kGoldenPrivacyRecordJson` in
`signed_envelope_verifier_test.cpp` and as the expected value in
`python/tests/test_signed_envelope.py`'s
`test_golden_hash_matches_the_cross_language_fixture` — neither side
derives its expected value from the implementation under test (Work
Package T's explicit requirement). Also proven live: a real PyNaCl
signature over Python's canonical bytes was accepted by the C++
verifier's independent recomputation, through the actual production
`GrpcCoordinatorClient.submit_result()` code path, over real mTLS.

See [signed-privacy-records.md](signed-privacy-records.md)'s
"Deviations from the parent specification's field list" section for
which of the specification's originally-requested fields were narrowed
or omitted (`record_version`, `clipping_mode`, `effective_batch_size`,
`skipped_optimizer_steps`, a separate `optimizer_steps` field) and why —
this codebase's actual Opacus integration does not track several of
them distinctly from what's already captured.

`fl.privacy.v1.SampleLevelLedgerEntry` (see
`cpp/coordinator/include/fl_coordinator/run_manager.hpp`'s
`SampleLevelLedgerEntry` struct) still does not itself carry these
fields — they live on the new, separately-signed
`SignedSamplePrivacyRecord` instead, bound to the ledger entry via an
explicit binding check (see signed-privacy-records.md) rather than by
adding fields to the ledger entry itself.

## Key-Rotation Request Hash (implemented and live-validated)

`rotation_payload_hash_input` (`signed_envelope_verifier.cpp` C++;
`signed_envelope.py` Python) binds to all 7 fields of
`fl.worker.v1.WorkerKeyRotationPayload`: `schema_version`, `worker_id`,
`current_signing_key_id`, `new_signing_key_id`, `new_public_key_hex`,
`new_key_expires_at_unix_s`, `requested_grace_period_seconds`, in
alphabetical key order. Rejects NaN/infinite values and a negative
`requested_grace_period_seconds` before hashing. Proven cross-language
via a real, reviewed golden fixture (the identical canonical JSON
string is hardcoded in both `signed_envelope_verifier_test.cpp`'s
`kGoldenRotationJson` and `test_signed_envelope.py`'s
`test_golden_hash_matches_the_cross_language_fixture`) and live, via a
real signed rotation request accepted by the coordinator through the
actual production `GrpcCoordinatorClient.rotate_signing_key()` code
path. See [key-rotation.md](key-rotation.md).

## What this does not prove

Signing and hashing a message authenticates *who sent it and that it
was not altered in transit* — never that its *content is correct*. A
correctly signed `SampleLevelLedgerEntry` still only proves "the worker
holding this signing key asserted this epsilon value," exactly as
already stated in
[privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)'s
trust model: "the coordinator stores and relays this value without
recomputing or verifying it." Authenticity and monotonicity checks
(once implemented) would detect a *replayed* or *tampered* privacy
record, and a lower-epsilon-without-reset record — they would not
detect a worker that computed its own DP-SGD accounting incorrectly in
the first place.
