#pragma once

// Secure aggregation provider interface, session configuration
// contract, and cohort state machine -- Secure Aggregation Protocol
// Foundation and No-Dropout Masked-Sum Core slice, Work Packages B, C,
// D, E. See docs/secure-cohort-lifecycle.md and
// docs/secure-aggregation-protocol-foundation.md.
//
// Deliberately protobuf-free and gRPC-free, like replay_protection_store.hpp
// and security_event.hpp -- pure session/state-machine logic with no
// cryptographic-primitive dependency, so it builds and is unit-testable
// on this Windows/MSVC development machine. The gRPC-gated RPC handlers
// that would actually create/drive a live session over the network are
// explicitly Tier 2, deferred work -- see
// docs/secure-aggregation-protocol-foundation.md's scope statement.
// This header defines the *contract* every future RPC handler must
// honor, not a live handler itself.

#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "fl_coordinator/secure_aggregation_encoding.hpp"

namespace fl::coordinator {

// Every provider a run can select -- explicit per run, never an
// implicit default (Work Package B: "no silent fallback to NONE").
// The experimental/no-dropout/honest-client-dependent qualifier is
// part of the name itself, not a footnote, so no caller can construct
// or log this value without the limitation being visible right there.
enum class SecureAggregationProvider {
    kNone,
    kSecureAggregationNoDropoutExperimental,
};

std::string to_string(SecureAggregationProvider provider);

// The cohort's lifecycle -- Work Package D's exact required state
// list, in the exact required order.
enum class CohortState {
    kCohortForming,
    kKeyAdvertisement,
    kCohortFrozen,
    kMaskedUpdateCollection,
    kAggregateValidation,
    kCompleted,
    kAborted,
    kFailed,
};

std::string to_string(CohortState state);

// Every reason a session can abort -- Work Package AE's event-type
// list distilled into one typed enum shared by the state machine, the
// event emitted when transitioning to kAborted, and (once wired, Tier
// 2) the Go/web observability surface. `kDropout` is the one this
// slice's own "no-dropout" name exists to make loud: any required
// participant missing after cohort freeze is this reason, always an
// abort, never a partial-aggregate fallback.
enum class SecureAggregationAbortReason {
    kNone,  // only valid on a non-aborted session
    kDropout,
    kDeadlineExceeded,
    kCohortMismatch,
    kEncodingRejected,
    kOverflowRejected,
    kMaskCancellationFailed,
    kCoordinatorRestart,
    kSessionExpired,
    kManualAbort,
    kInvalidTransitionRequested,
    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area AB.
    kPrivacyModeIncompatible,
};

std::string to_string(SecureAggregationAbortReason reason);

class CohortStateMachineError : public std::runtime_error {
  public:
    explicit CohortStateMachineError(const std::string& what);
};

// One transition record -- kept for audit/debugging, mirrors this
// project's existing "every mutation is recorded, not just the
// current state" convention (see e.g. AcceptedTaskJournal).
struct CohortStateTransition {
    CohortState from = CohortState::kCohortForming;
    CohortState to = CohortState::kCohortForming;
    double timestamp_unix_s = 0.0;
    std::string reason;  // human-readable; abort_reason below is the typed value
};

// Explicit transition validation and history -- Work Package D's "no
// implicit transition" requirement enforced mechanically: the only way
// to change `state_` is through transition_to()/abort()/fail(), each
// of which checks the from/to pair against a fixed allow-list before
// mutating anything.
class CohortStateMachine {
  public:
    explicit CohortStateMachine(std::string session_id);

    [[nodiscard]] const std::string& session_id() const;
    [[nodiscard]] CohortState state() const;
    [[nodiscard]] bool is_terminal() const;
    [[nodiscard]] const std::vector<CohortStateTransition>& history() const;
    [[nodiscard]] SecureAggregationAbortReason abort_reason() const;
    [[nodiscard]] const std::string& failure_reason() const;

    // Advances CohortForming -> KeyAdvertisement -> CohortFrozen ->
    // MaskedUpdateCollection -> AggregateValidation -> Completed, one
    // step at a time. Throws CohortStateMachineError for any
    // unlisted (from, to) pair -- including attempting to skip a
    // state, go backwards, or transition out of a terminal state.
    void transition_to(CohortState next, double timestamp_unix_s, const std::string& reason = "");

    // Any non-terminal state -> Aborted. Always allowed (Work Package
    // D: "any active state -> ABORTED"); a no-op-with-error if the
    // session is already terminal (an already-completed/aborted/failed
    // session cannot be aborted again -- this is itself a caller
    // error, surfaced as an exception rather than silently ignored).
    void abort(SecureAggregationAbortReason reason,
               double timestamp_unix_s,
               const std::string& detail = "");

    // Any state -> Failed, for an unexpected internal error (Work
    // Package D: "unexpected internal failure -> FAILED") -- unlike
    // abort(), this is allowed even from a terminal state transition
    // attempt gone wrong (e.g. a bug in calling code), since a FAILED
    // marking must never itself be blocked by the state machine's own
    // invariants.
    void fail(const std::string& reason, double timestamp_unix_s);

  private:
    std::string session_id_;
    CohortState state_;
    std::vector<CohortStateTransition> history_;
    SecureAggregationAbortReason abort_reason_ = SecureAggregationAbortReason::kNone;
    std::string failure_reason_;
};

// Versioned session configuration -- Work Package C's exact required
// field list. This is the *contract*, constructed and validated
// locally in this slice; a Tier 2 pass wires it into a real signed
// coordinator-task binding (Work Package K) and a real wire message.
struct SecureAggregationSessionConfig {
    std::uint32_t schema_version = 1;
    std::uint32_t protocol_version = 1;
    SecureAggregationProvider provider = SecureAggregationProvider::kNone;

    std::string session_id;
    std::string run_id;
    std::uint64_t round_id = 0;
    std::string model_version;
    std::string aggregation_algorithm;  // e.g. "fedavg" -- Work Package AA's initial scope

    std::uint64_t cohort_size = 0;
    std::uint64_t minimum_cohort_size = 0;
    std::vector<std::string> ordered_participant_ids;

    std::string tensor_manifest_hash;
    std::string model_manifest_hash;

    FixedPointEncodingProfile fixed_point_profile;
    // "Field or ring profile" (Work Package C) -- this project's
    // domain is always a fixed 2^64 ring (see
    // secure_aggregation_encoding.hpp), so this field records that
    // choice as an explicit string rather than a boolean/enum with
    // only one real value, so a future prime-field profile can be
    // added without an incompatible schema change.
    std::string domain_profile = "ring_mod_2_64";

    double scale_factor =
        0.0;  // mirrors fixed_point_profile.scale_factor, kept explicit per Work Package C
    double max_absolute_update_bound = 0.0;
    std::uint64_t max_client_weight = 0;
    std::uint64_t max_aggregate_bound = 0;

    std::string mask_generator_profile = "chacha20_ietf";
    std::string key_agreement_profile = "x25519";
    std::string key_derivation_profile = "hkdf_sha256";

    double session_created_at_unix_s = 0.0;
    double key_advertisement_deadline_unix_s = 0.0;
    double masked_update_deadline_unix_s = 0.0;
    double session_expiry_unix_s = 0.0;

    std::string coordinator_signing_key_id;
    // Populated by compute_session_configuration_hash() below -- never
    // set directly by a caller, matching this project's existing
    // "hash fields are derived, not caller-supplied" convention (see
    // coordinator_task_signing.cpp).
    std::string session_configuration_hash;
};

// Canonical, domain-separated hash over every field in
// SecureAggregationSessionConfig except session_configuration_hash
// itself -- Work Package C's "session configuration hash" field.
// Deliberately declared here (pure function signature) but
// implemented in the gRPC-gated secure_aggregation_crypto.cpp (Tier 1
// of the crypto module, not this file), since it needs the same
// vetted SHA-256 primitive every other canonical hash in this codebase
// uses -- see docs/secure-aggregation-protocol-foundation.md's module
// layout for why hashing lives in the gRPC-gated module while the
// struct definition itself does not.
[[nodiscard]] std::string compute_session_configuration_hash(
    const SecureAggregationSessionConfig& config);

}  // namespace fl::coordinator
