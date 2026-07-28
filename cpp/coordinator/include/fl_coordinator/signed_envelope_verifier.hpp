#pragma once

// Verification of signed worker envelopes -- Message Authenticity
// Enforcement and Identity Lifecycle slice, Work Packages B/C/D. See
// docs/signed-worker-envelopes.md and docs/payload-hashing.md.
//
// The envelope counterpart to capability_statement_verifier.hpp: that
// file verifies fl.worker.v1.SignedCapabilityStatement (used once, at
// registration, where the coordinator has no prior key to trust yet --
// trust-on-first-use); this file verifies
// fl.worker.v1.SignedWorkerEnvelope (used on every subsequent
// security-sensitive worker message, where the coordinator already has
// a registered signing key to verify against -- see
// WorkerIdentityRegistry). Concretely, unlike SignedCapabilityStatement,
// SignedWorkerEnvelope carries no signing_public_key of its own: the
// caller must resolve it from WorkerIdentityRegistry using the
// already-certificate-bound worker_id, never trust a self-asserted key
// on the envelope itself.
//
// OpenSSL-backed (EVP_PKEY_ED25519), gRPC-gated build only -- same
// reasoning as capability_statement_verifier.hpp's header comment.
//
// Deliberately does NOT perform: worker_id/certificate binding (the
// caller's job, via peer_identity.hpp), WorkerIdentityRegistry status
// checks (the caller's job), or nonce/sequence replay validation (the
// caller's job, via ReplayProtectionStore -- kept as an explicit,
// separate pipeline step rather than folded in here, matching
// capability_statement_verifier.hpp's identical "nonce replay is a
// separate store" design and Work Package D's literal step-by-step
// pipeline).

#include <cstdint>
#include <string>

namespace fl::worker::v1 {
class SignedWorkerEnvelope;
class WorkerHeartbeatRequest;
class WorkerKeyRotationPayload;
class SignedWorkerSecurityEventBatch;
class SecureAggregationKeyAdvertisement;
class MaskedClientUpdate;
class SignedUserLevelPrivacyAttestation;
class SignedAdaptiveClippingBinding;
}  // namespace fl::worker::v1

namespace fl::coordinator::v1 {
class SubmitClientResultRequest;
}  // namespace fl::coordinator::v1

namespace fl::privacy::v1 {
class SignedSamplePrivacyRecord;
}  // namespace fl::privacy::v1

namespace fl::coordinator {

inline constexpr std::uint32_t kSignedWorkerEnvelopeSchemaVersion = 1;

struct EnvelopeVerificationResult {
    bool valid = false;
    std::string reason;          // human-readable; always set, even when valid == true ("ok")
    std::string rejection_code;  // stable machine-readable code; empty when valid == true
};

// Canonical JSON encoding of `envelope`'s own metadata fields
// (schema_version through signing_key_id, in alphabetical key order --
// same convention as capability_statement_verifier.hpp's
// canonical_capability_payload_json) -- this is what actually gets
// Ed25519-signed, after a domain-separation prefix is prepended (see
// envelope_signing_bytes below). payload_hash is one of the fields
// included here (it is metadata *about* the domain payload, from the
// envelope's point of view), but the domain payload's own fields
// (e.g. WorkerHeartbeatRequest's worker_id/status/current_task_id) are
// never included here -- see heartbeat_payload_hash_input for where
// those are actually hashed.
[[nodiscard]] std::string canonical_envelope_metadata_json(
    const fl::worker::v1::SignedWorkerEnvelope& envelope);

// The exact bytes verify_signed_envelope checks the Ed25519 signature
// against: a fixed domain-separation prefix (distinguishing "this is a
// SignedWorkerEnvelope signature" from any other structure signed by
// the same worker signing key, e.g. SignedCapabilityStatement -- see
// docs/canonical-security-serialization.md's previously-flagged gap,
// closed by this function) followed by canonical_envelope_metadata_json's
// output.
[[nodiscard]] std::string envelope_signing_bytes(
    const fl::worker::v1::SignedWorkerEnvelope& envelope);

// Heartbeat payload hash input (docs/payload-hashing.md's "Heartbeat
// Hash"): binds to exactly the fields WorkerHeartbeatRequest carries on
// the wire today -- worker_id, status, current_task_id -- plus
// envelope.issued_at() as the heartbeat's timestamp. Deliberately does
// NOT bind to capacity metadata, software_version, or build_id: none of
// those are part of WorkerHeartbeatRequest's current wire format (they
// are already asserted once, authoritatively, in the signed capability
// statement at registration time -- re-asserting them on every
// heartbeat would need new proto fields nothing else uses yet, and is
// left to a follow-on pass rather than invented here).
[[nodiscard]] std::string heartbeat_payload_hash_input(
    const fl::worker::v1::WorkerHeartbeatRequest& request,
    const fl::worker::v1::SignedWorkerEnvelope& envelope);

// Client result payload hash input (docs/payload-hashing.md's "Client
// Result Hash"): binds to schema_version, run_id, round_id, task_id
// (SubmitClientResultRequest.task_id -- also serves as "update ID", the
// coordinator's own domain layer already treats the two as the same
// value), client_id, worker_id, model version (base_model_version),
// algorithm, sample_count, step_count (local_step_count),
// update_norm, nonce, completion_timestamp, an ordered (by tensor
// name, ascending) canonical tensor manifest (name/shape/dtype/
// byte_length/checksum per tensor -- never raw values, which are
// covered once by checksum, not duplicated here), an ordered (by
// metric name, ascending) canonical training-metrics list, and a
// nested canonical object for personalization_metrics/sample_level_privacy
// when present (an empty object when absent -- the canonical empty
// representation for these two optional sub-messages).
//
// Returns an error reason (non-empty) instead of a hash input string
// if any metric or tensor-adjacent floating-point field is NaN/Inf, or
// if a tensor descriptor is structurally invalid (empty name, or a
// shape/byte_length/checksum that fails basic sanity checks) -- these
// must be rejected before hashing, not silently hashed and only
// discovered later by domain validation.
struct ClientResultPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] ClientResultPayloadHashResult client_result_payload_hash_input(
    const fl::coordinator::v1::SubmitClientResultRequest& request);

// Sample privacy record payload hash input (docs/signed-privacy-records.md,
// docs/payload-hashing.md's "Sample Privacy Record Hash"): binds to
// every field of fl.privacy.v1.SignedSamplePrivacyRecord in alphabetical
// key order, matching client_result_payload_hash_input's canonicalization
// convention exactly. Rejects (ok == false) if epsilon, delta,
// noise_multiplier, max_grad_norm, sample_rate, budget_target_epsilon,
// or budget_target_delta is NaN/infinite, or if epsilon/delta/
// noise_multiplier/sample_rate/max_grad_norm is negative (a physically
// meaningless value for any of these -- see
// docs/signed-privacy-records.md's canonicalization rules).
struct PrivacyRecordPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] PrivacyRecordPayloadHashResult sample_privacy_record_payload_hash_input(
    const fl::privacy::v1::SignedSamplePrivacyRecord& record);

// Verifies, in order: schema_version; message_type matches
// expected_message_type (the wire enum value the calling RPC handler
// expects -- e.g. MESSAGE_TYPE_WORKER_HEARTBEAT for Heartbeat);
// payload_hash matches SHA-256(payload_hash_input) (the caller has
// already computed payload_hash_input via the appropriate per-message-
// type function, e.g. heartbeat_payload_hash_input); the Ed25519
// signature (verified against signing_public_key_hex, which the caller
// must have already resolved from WorkerIdentityRegistry -- never
// trusted from the wire) is valid over envelope_signing_bytes;
// expires_at is a real value strictly after now_unix_s; issued_at is
// not more than future_issued_tolerance_seconds ahead of now_unix_s
// (defends against a clock-skewed or malicious future-dated envelope
// being accepted just because it hasn't technically expired yet).
[[nodiscard]] EnvelopeVerificationResult verify_signed_envelope(
    const fl::worker::v1::SignedWorkerEnvelope& envelope,
    int expected_message_type,
    const std::string& payload_hash_input,
    const std::string& signing_public_key_hex,
    double now_unix_s,
    double future_issued_tolerance_seconds);

// SHA-256 hex digest of the raw bytes a hex-encoded Ed25519 public key
// decodes to. Used by SigningKeyRegistry callers to compute
// SigningKeyRecord::public_key_fingerprint (docs/signing-key-management.md)
// -- kept here, not in signing_key_registry.hpp/.cpp, so that store
// stays OpenSSL-free and buildable on this Windows/MSVC machine without
// a local gRPC toolchain. Returns an empty string if public_key_hex is
// not valid hex.
[[nodiscard]] std::string public_key_fingerprint_hex(const std::string& public_key_hex);

// Key-rotation-request payload hash input (docs/key-rotation.md,
// docs/payload-hashing.md): binds to every field of
// fl.worker.v1.WorkerKeyRotationPayload in alphabetical key order,
// matching client_result_payload_hash_input/
// sample_privacy_record_payload_hash_input's canonicalization
// convention exactly.
struct RotationPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] RotationPayloadHashResult rotation_payload_hash_input(
    const fl::worker::v1::WorkerKeyRotationPayload& payload);

// Worker security-event-batch payload hash input (Web Security Center,
// Event Centralization, and Security CI slice, Work Package L). Binds
// to every field of fl.worker.v1.SignedWorkerSecurityEventBatch --
// schema_version, worker_id, queue_depth_hint, and the `events` list --
// in alphabetical key order, matching every other *_payload_hash_input
// function's canonicalization convention. Unlike
// client_result_payload_hash_input's tensor/metric lists, the events
// list is NOT re-sorted: submission order is part of what the worker
// signed and is preserved verbatim (docs/security-event-centralization.md).
// This function hashes exactly the bytes the worker signed, whether or
// not any individual event within later turns out to be malformed --
// per-event bound/vocabulary validation (kSecurityEventMaxDetailKeys,
// unknown event_type/severity strings, etc) happens afterward, per
// event, and skips only that event rather than invalidating the whole
// batch's signature (see SubmitWorkerSecurityEvents in
// coordinator_service.cpp).
struct SecurityEventBatchPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] SecurityEventBatchPayloadHashResult security_event_batch_payload_hash_input(
    const fl::worker::v1::SignedWorkerSecurityEventBatch& batch);

// Secure Cohort Handshake and Signed Roster Runtime slice
// (docs/secure-cohort-handshake-foundation.md), Work item 8. Binds to
// every field of fl.worker.v1.SecureAggregationKeyAdvertisement in
// alphabetical key order, matching rotation_payload_hash_input's
// canonicalization convention exactly. Rejects (ok == false) if
// issued_at/expires_at is NaN/infinite.
struct SecureAggregationKeyAdvertisementPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] SecureAggregationKeyAdvertisementPayloadHashResult
secure_aggregation_key_advertisement_payload_hash_input(
    const fl::worker::v1::SecureAggregationKeyAdvertisement& advertisement);

// Masked Update Runtime and No-Dropout Secure FedAvg Finalization slice
// (docs/secure-aggregation-masked-update.md), Work Area L. Binds every
// field of fl.worker.v1.MaskedClientUpdate in alphabetical key order,
// masked_tensors sorted by tensor_name (not wire/insertion order) --
// matching python's masked_client_update_payload_hash_input exactly.
// Rejects (ok == false) if issued_at/expires_at/either quantization-
// error statistic is NaN/infinite.
struct MaskedClientUpdatePayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] MaskedClientUpdatePayloadHashResult masked_client_update_payload_hash_input(
    const fl::worker::v1::MaskedClientUpdate& update);

// Secure User-Level Differential Privacy Runtime slice, Work Areas I/J.
// Binds every field of fl.worker.v1.SignedUserLevelPrivacyAttestation
// EXCEPT signing_key_id/payload_hash/signature themselves (the same
// "hash everything but the signature fields" convention every payload-
// hash function in this file already follows), alphabetical key order.
// Rejects (ok == false) if issued_at/expires_at/clip_norm/
// effective_sensitivity is NaN/infinite.
struct UserLevelPrivacyAttestationPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] UserLevelPrivacyAttestationPayloadHashResult
user_level_privacy_attestation_payload_hash_input(
    const fl::worker::v1::SignedUserLevelPrivacyAttestation& attestation);

// Mirrors coordinator_task_signing.cpp's coordinator_task_signing_bytes
// pattern exactly: canonical JSON of every metadata field (including
// payload_hash and signing_key_id) EXCLUDING signature itself --
// SignedUserLevelPrivacyAttestation is self-contained (no wrapping
// SignedWorkerEnvelope), so this plays the role
// envelope_signing_bytes/coordinator_task_signing_bytes each play for
// their own self/wrapper-signed structures. Exported (not kept
// anonymous-namespace-private) for the same reason
// coordinator_task_signing_bytes is: real round-trip test coverage
// needs to construct genuinely valid signed bytes without duplicating
// this canonicalization a second time.
[[nodiscard]] std::string user_level_privacy_attestation_signing_bytes(
    const fl::worker::v1::SignedUserLevelPrivacyAttestation& attestation);

// Verifies a SignedUserLevelPrivacyAttestation's own Ed25519 signature
// and expiry -- self-contained (the attestation carries its own
// signing_key_id/payload_hash/signature, not a SignedWorkerEnvelope
// wrapper), so this is a sibling of verify_signed_envelope rather than
// a call through it. The caller (coordinator_service.cpp) is
// responsible for checking that `signing_public_key_hex` corresponds
// to the SAME signing_key_id already resolved+verified for the outer
// MaskedClientUpdate's envelope, and for every structural binding
// check (worker/client/session/task/model_version/round_id match the
// outer update) -- this function only proves the attestation's own
// bytes are authentic and unexpired, nothing about which update it is
// bound to.
[[nodiscard]] EnvelopeVerificationResult verify_user_level_privacy_attestation(
    const fl::worker::v1::SignedUserLevelPrivacyAttestation& attestation,
    const std::string& signing_public_key_hex,
    double now_unix_s);

// Secure Adaptive Clipping with Private Indicator Aggregation slice.
// Identical shape/discipline to the three UserLevelPrivacyAttestation
// functions immediately above, for fl.worker.v1.SignedAdaptiveClippingBinding
// instead -- see docs/secure-adaptive-clipping-semantics.md section 15.
struct AdaptiveClippingBindingPayloadHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string reason;      // set only when ok == false
};
[[nodiscard]] AdaptiveClippingBindingPayloadHashResult adaptive_clipping_binding_payload_hash_input(
    const fl::worker::v1::SignedAdaptiveClippingBinding& binding);

[[nodiscard]] std::string adaptive_clipping_binding_signing_bytes(
    const fl::worker::v1::SignedAdaptiveClippingBinding& binding);

// Same caller responsibilities as verify_user_level_privacy_attestation
// above (this only proves the binding's own bytes are authentic and
// unexpired -- the caller checks the signing key matches the outer
// envelope's and every structural field binds to the same submission).
[[nodiscard]] EnvelopeVerificationResult verify_adaptive_clipping_binding(
    const fl::worker::v1::SignedAdaptiveClippingBinding& binding,
    const std::string& signing_public_key_hex,
    double now_unix_s);

}  // namespace fl::coordinator
