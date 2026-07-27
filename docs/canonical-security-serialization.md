# Canonical Security Serialization

**Status: Implemented and cross-language-parity-tested for several
structures now (signed capability statements, signed worker envelopes,
client results, privacy records, key rotation, and — this slice —
coordinator-signed tasks, all Python ↔ C++). Deferred for everything
else** (a Go implementation, protobuf-based canonicalization).

## The rule actually implemented

See [signed-capabilities.md](signed-capabilities.md) and
`fl_platform.security.capability_statement._canonical_bytes`:

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
```

| Rule | This pass's answer |
|---|---|
| Field ordering | Explicit lexicographic key sort (`sort_keys=True`) — never relies on dict insertion order, which is deterministic within one Python process but not a guarantee any other language's map/dict shares. |
| Integer encoding | Python `int` via `json.dumps`'s default (plain decimal digits) — `cpp/coordinator/src/capability_statement_verifier.cpp` matches this trivially (protobuf `uint32`/`uint64` stream via `operator<<`, same digit sequence). |
| Float encoding | Python `float.__repr__` (shortest round-trip decimal). C++ matches via `std::to_chars` (also shortest round-trip) plus a `.0` suffix for whole-number values, since `to_chars` omits the decimal point where Python's JSON encoder never does (`1785003600.0` vs. `to_chars`'s bare `1785003600`) — this specific case is covered by the golden-vector test (see below), which deliberately includes a whole-number `expires_at`. **Only verified correct for realistic Unix-timestamp magnitudes** (`issued_at`/`expires_at`, the only float fields in this payload) — not a general-purpose JSON-float-encoder parity claim for arbitrary magnitudes (extreme values where Python and libstdc++ might choose fixed vs. scientific notation differently are untested). |
| Boolean encoding | JSON `true`/`false` — standard, low-risk. |
| String encoding | UTF-8, with `ensure_ascii=True` escaping every non-ASCII character identically regardless of the underlying platform's default encoding. `canonical_capability_payload_json`'s `json_escape_string` implements the same escaping in C++: the standard short escapes (`\"`, `\\`, `\n`, `\r`, `\t`, `\b`, `\f`), `\u00XX` for other C0 control characters, and `\uXXXX` (with surrogate pairs above `U+FFFF`) for any decoded UTF-8 code point above `U+007F` — decoding malformed/truncated UTF-8 byte-by-byte rather than throwing, since this function must never crash on attacker-controlled input (a malformed sequence simply fails to round-trip, which fails signature verification downstream instead). |
| Unicode normalization | **Not implemented.** Two strings that are visually identical but differently normalized (NFC vs NFD) would canonicalize to different bytes. Not currently a concern (all current fields are ASCII: worker IDs, version strings, hex-encoded hashes) but not guarded against for arbitrary future string fields. |
| Timestamp encoding | Python `float` (Unix seconds) — same caveat as integer/float encoding above. |
| List ordering | Preserved as given by the caller (algorithm/model lists are not sorted) — this means two logically-equivalent statements with reordered lists produce different signatures. Acceptable today since the payload is always constructed by one code path in a fixed order; would need an explicit rule if list order ever becomes caller-controlled. |
| Enum encoding | String values (e.g. accountant names) — no numeric enum encoding used. |
| Empty-field behavior | Empty tuples serialize as `[]`; no special-casing. |
| Optional-field behavior | All fields in `CapabilityStatementPayload` are required (dataclass fields, several with defaults but none `Optional`) — there is no "field present vs. field omitted" ambiguity to canonicalize. |
| Byte-array encoding | Not applicable yet — no field in the current payload is raw bytes (`payload_hash`/`signature` are hex strings, encoded after canonicalization, not part of the signed content itself). |
| Version field | `schema_version` (int, currently `1`) is itself part of the signed payload. |
| Domain-separation prefix | **Not implemented for `SignedCapabilityStatement` itself** (still no distinguishing prefix on its own signature) — but every structure added since uses one: `SignedWorkerEnvelope` (`"fl.worker.v1.SignedWorkerEnvelope\x00"`) and `SignedCoordinatorTask` (`"FL_PLATFORM_COORDINATOR_TASK_V1\x00"`, with each of its five configuration hashes plus the task payload hash using its *own* distinct prefix — see [task-configuration-hashes.md](task-configuration-hashes.md)) both prepend a fixed prefix before hashing/signing, closing the gap this row originally flagged for every structure introduced after capability statements. |

## How cross-language parity was actually proven, not assumed

Two independent, real tests, not a shared golden-vector *generator*
(each side computed its own output separately, from the same fixed
input, then compared):

1. `capability_statement_verifier_test.cpp` embeds a literal string —
   `kGoldenPayloadJson` — that was produced by *actually running*
   Python's `json.dumps(payload, sort_keys=True, separators=(",",":"),
   ensure_ascii=True)` on a fixed payload, then pasted verbatim into the
   C++ test as a constant. `canonical_capability_payload_json` (C++) is
   asserted to produce that exact byte string from the equivalent
   protobuf message.
2. A live, containerized, real-mTLS test: a real PyNaCl Ed25519
   signature, computed over Python's canonical bytes, is sent over the
   wire and verified by OpenSSL against the *C++* encoder's canonical
   bytes. If the two encoders disagreed on a single byte, the SHA-256
   `payload_hash` comparison inside `verify_capability_statement` (C++)
   would fail first, before the signature is even checked — this
   passing is only possible because both sides produced identical
   bytes. See [signed-capabilities.md](signed-capabilities.md)'s "Live
   coordinator wiring" section for the full scenario list.

## What this is not

* **Not deterministic protobuf serialization** — the closure gate's
  originally preferred option for real cross-language parity. This
  pass's rule is a plain canonical-JSON convention with a hand-written,
  field-set-specific C++ encoder (not a general JSON library) — this
  works because the payload's field set and types are fixed and known;
  it would not automatically extend to an arbitrary new structure
  without writing (and cross-language-testing) an equivalent
  hand-written encoder for it.
* **No Go implementation exists yet.** Only Python and C++ have been
  built and tested against each other.
* **Extended since to worker envelopes, client results, privacy
  records, key rotation, and coordinator-signed tasks** — see
  [signed-worker-envelopes.md](signed-worker-envelopes.md) and
  [signed-coordinator-tasks.md](signed-coordinator-tasks.md). Each
  extension required (and got) its own real cross-language golden
  fixture — this rule does not automatically transfer to a new
  structure without one (see "What this is not" below).
* **The C++ float encoder is not a general-purpose JSON-float encoder**
  — see the "Float encoding" table row above. It is verified correct
  for the specific magnitudes this payload's `issued_at`/`expires_at`
  fields actually take (Unix timestamps), not for arbitrary doubles.

Do not sign a *new* structure with this module's canonicalization rule
and assume it will verify identically from the C++ implementation
without first writing and passing a real cross-language test vector for
that structure specifically — the parity proven here covers exactly the
`SignedCapabilityStatement` field set, nothing broader.
