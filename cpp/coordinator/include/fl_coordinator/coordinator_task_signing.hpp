#pragma once

// Signing (and, for test round-trips, verifying) of
// fl.coordinator.v1.SignedCoordinatorTask -- Coordinator-Signed Tasks
// slice, Work Packages D/F/G/I. See docs/signed-coordinator-tasks.md
// and docs/task-configuration-hashes.md.
//
// The coordinator-signing counterpart to signed_envelope_verifier.hpp
// (which only verifies worker-signed messages): this file is the
// first place the coordinator actually *produces* a signature, over
// its own outgoing AcquireTask response. OpenSSL-backed
// (EVP_PKEY_ED25519), gRPC-gated build only -- same reasoning as
// signed_envelope_verifier.hpp's header comment.
//
// Scope: the five configuration hashes and the task payload hash are
// computed only over fields fl.coordinator.v1.ClientTrainingTask
// actually carries on the wire today -- see
// docs/task-configuration-hashes.md for the exact field list per hash
// and why (this codebase's established discipline: never invent
// fields nothing else populates).

#include <cstdint>
#include <string>

namespace fl::coordinator::v1 {
class ClientTrainingTask;
class SignedCoordinatorTask;
}  // namespace fl::coordinator::v1

namespace fl::coordinator {

struct CoordinatorSigningIdentity;

inline constexpr std::uint32_t kSignedCoordinatorTaskSchemaVersion = 1;

// One of the five configuration hashes, or the task payload hash.
// Returns ok == false (with `reason` set) if any floating-point field
// involved is NaN/Inf -- must be rejected before hashing, matching
// client_result_payload_hash_input's identical convention.
struct TaskHashResult {
    bool ok = false;
    std::string hash_input;  // valid only when ok == true
    std::string hash_hex;    // SHA-256 hex of hash_input; valid only when ok == true
    std::string reason;      // set only when ok == false
};

[[nodiscard]] TaskHashResult training_configuration_hash(const fl::coordinator::v1::ClientTrainingTask& task);
[[nodiscard]] TaskHashResult model_configuration_hash(const fl::coordinator::v1::ClientTrainingTask& task);
[[nodiscard]] TaskHashResult dataset_partition_hash(const fl::coordinator::v1::ClientTrainingTask& task);
[[nodiscard]] TaskHashResult privacy_configuration_hash(const fl::coordinator::v1::ClientTrainingTask& task);
[[nodiscard]] TaskHashResult personalization_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task);
[[nodiscard]] TaskHashResult task_payload_hash(const fl::coordinator::v1::ClientTrainingTask& task);

// Secure Cohort Handshake and Signed Roster Runtime slice
// (docs/secure-cohort-handshake-foundation.md), Work item 4. A sibling
// hash, exactly like personalization_configuration_hash -- deliberately
// NOT folded into task_payload_hash's own canonical JSON (that
// computation is stable and already live; this hash is bound into the
// real signature via coordinator_task_signing_bytes instead, the same
// way personalization_configuration_hash already is). Returns
// {"secure_aggregation_active":false} when the task has no secure
// aggregation binding, matching privacy_configuration_hash's identical
// "inactive" shape convention.
[[nodiscard]] TaskHashResult secure_aggregation_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task);

// Secure User-Level Differential Privacy Runtime slice, Work Area E. A
// second sibling hash, same convention as
// secure_aggregation_configuration_hash immediately above. Returns
// {"secure_user_level_dp_active":false} when the task's secure
// aggregation binding has no active user-level-DP configuration.
[[nodiscard]] TaskHashResult secure_user_level_dp_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task);

// The exact bytes a coordinator task signature is computed over: a
// fixed domain-separation prefix followed by canonical JSON of
// `signed_task`'s own metadata fields (schema_version through
// task_payload_hash, alphabetical key order), excluding `signature`
// itself. Mirrors envelope_signing_bytes's identical shape.
[[nodiscard]] std::string coordinator_task_signing_bytes(
    const fl::coordinator::v1::SignedCoordinatorTask& signed_task);

struct SignCoordinatorTaskParams {
    std::string worker_id;
    std::string task_id;
    std::string lease_id;
    std::uint32_t attempt{0};
    double issued_at{0.0};
    double expires_at{0.0};
    std::string nonce;
    std::uint64_t sequence_number{0};
};

struct SignCoordinatorTaskResult {
    bool ok = false;
    std::string reason;  // set only when ok == false
};

// Computes all five configuration hashes plus the task payload hash
// from `task` (already fully populated by AcquireTask's existing wire
// mapping), then signs the result with `identity` and writes every
// field of `out` (including `signature`). `task` itself is never
// mutated -- the caller attaches `out` as ClientTrainingTask.signed_task
// separately.
[[nodiscard]] SignCoordinatorTaskResult sign_coordinator_task(
    const fl::coordinator::v1::ClientTrainingTask& task, const SignCoordinatorTaskParams& params,
    const CoordinatorSigningIdentity& identity, fl::coordinator::v1::SignedCoordinatorTask& out);

// Test/round-trip-only: verifies signed_task's Ed25519 signature
// against signing_public_key_hex. Does not check expiry, worker
// binding, or replay -- that is the (Python) worker's job in
// production; this exists so a C++ unit test can prove the signature
// this file produces is genuinely valid, not merely well-formed.
[[nodiscard]] bool verify_coordinator_task_signature(
    const fl::coordinator::v1::SignedCoordinatorTask& signed_task,
    const std::string& signing_public_key_hex);

}  // namespace fl::coordinator
