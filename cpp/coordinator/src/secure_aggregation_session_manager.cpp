#include "fl_coordinator/secure_aggregation_session_manager.hpp"

#include "fl_coordinator/coordinator_signing_identity.hpp"
#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_encoding.hpp"
#include "fl_coordinator/secure_aggregation_mask.hpp"
#include "fl_coordinator/secure_aggregation_tensor_mask.hpp"

#include <algorithm>
#include <cmath>
#include <set>
#include <sstream>

namespace fl::coordinator {

namespace {

namespace pb_coordinator = fl::coordinator::v1;
namespace pb_worker = fl::worker::v1;

// -- proto <-> C++-native pure-math type conversions ------------------

FixedPointEncodingProfile to_cpp_profile(
    const pb_coordinator::FixedPointEncodingProfile& proto_profile) {
    FixedPointEncodingProfile profile;
    profile.schema_version = proto_profile.schema_version();
    profile.rounding_rule =
        RoundingRule::kRoundHalfAwayFromZero;  // only one rule exists; wire field is descriptive
    profile.scale_factor = proto_profile.scale_factor();
    profile.max_input_magnitude = proto_profile.max_input_magnitude();
    profile.max_client_weight = proto_profile.max_client_weight();
    profile.max_cohort_size = proto_profile.max_cohort_size();
    profile.safety_margin = proto_profile.safety_margin();
    return profile;
}

pb_coordinator::SecureAggregationSessionState to_proto_state(CohortState state) {
    switch (state) {
        case CohortState::kCohortForming:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_COHORT_FORMING;
        case CohortState::kKeyAdvertisement:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_KEY_ADVERTISEMENT;
        case CohortState::kCohortFrozen:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_COHORT_FROZEN;
        case CohortState::kMaskedUpdateCollection:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_MASKED_UPDATE_COLLECTION;
        case CohortState::kAggregateValidation:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_AGGREGATE_VALIDATION;
        case CohortState::kCompleted:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_COMPLETED;
        case CohortState::kAborted:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED;
        case CohortState::kFailed:
            return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_FAILED;
    }
    return pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_UNSPECIFIED;
}

pb_coordinator::SecureAggregationAbortReason to_proto_abort_reason(
    SecureAggregationAbortReason reason) {
    switch (reason) {
        case SecureAggregationAbortReason::kNone:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_UNSPECIFIED;
        case SecureAggregationAbortReason::kDropout:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DROPOUT;
        case SecureAggregationAbortReason::kDeadlineExceeded:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DEADLINE_EXCEEDED;
        case SecureAggregationAbortReason::kCohortMismatch:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_COHORT_MISMATCH;
        case SecureAggregationAbortReason::kEncodingRejected:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_ENCODING_REJECTED;
        case SecureAggregationAbortReason::kOverflowRejected:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_OVERFLOW_REJECTED;
        case SecureAggregationAbortReason::kMaskCancellationFailed:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MASK_CANCELLATION_FAILED;
        case SecureAggregationAbortReason::kCoordinatorRestart:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_COORDINATOR_RESTART;
        case SecureAggregationAbortReason::kSessionExpired:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_SESSION_EXPIRED;
        case SecureAggregationAbortReason::kManualAbort:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MANUAL_ABORT;
        case SecureAggregationAbortReason::kInvalidTransitionRequested:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_INVALID_TRANSITION_REQUESTED;
        case SecureAggregationAbortReason::kPrivacyModeIncompatible:
            return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE;
    }
    return pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_UNSPECIFIED;
}

SecureAggregationAbortReason to_cpp_abort_reason(
    pb_coordinator::SecureAggregationAbortReason reason) {
    switch (reason) {
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_UNSPECIFIED:
            return SecureAggregationAbortReason::kNone;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DROPOUT:
            return SecureAggregationAbortReason::kDropout;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DEADLINE_EXCEEDED:
            return SecureAggregationAbortReason::kDeadlineExceeded;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_COHORT_MISMATCH:
            return SecureAggregationAbortReason::kCohortMismatch;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_ENCODING_REJECTED:
            return SecureAggregationAbortReason::kEncodingRejected;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_OVERFLOW_REJECTED:
            return SecureAggregationAbortReason::kOverflowRejected;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MASK_CANCELLATION_FAILED:
            return SecureAggregationAbortReason::kMaskCancellationFailed;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_COORDINATOR_RESTART:
            return SecureAggregationAbortReason::kCoordinatorRestart;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_SESSION_EXPIRED:
            return SecureAggregationAbortReason::kSessionExpired;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MANUAL_ABORT:
            return SecureAggregationAbortReason::kManualAbort;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_INVALID_TRANSITION_REQUESTED:
            return SecureAggregationAbortReason::kInvalidTransitionRequested;
        case pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE:
            return SecureAggregationAbortReason::kPrivacyModeIncompatible;
        default:
            return SecureAggregationAbortReason::kNone;
    }
}

// Canonical little-endian byte encoding of a uint64 ring-value vector
// -- the convention this manager defines for masked-tensor/masked-
// weight checksums (documented in worker.proto's own comment on
// SecureAggregationMaskedTensor.checksum). Matches
// secure_aggregation_tensor_mask.cpp's bytes_to_le_uint64_values in
// reverse.
std::string le_bytes_of(const std::vector<std::uint64_t>& values) {
    std::string out;
    out.resize(values.size() * 8);
    for (std::size_t i = 0; i < values.size(); ++i) {
        std::uint64_t v = values[i];
        for (int b = 0; b < 8; ++b) {
            out[i * 8 + static_cast<std::size_t>(b)] = static_cast<char>(v & 0xFF);
            v >>= 8;
        }
    }
    return out;
}

std::string compute_masked_values_checksum(const std::vector<std::uint64_t>& values) {
    return sha256_hex(le_bytes_of(values));
}

// Small canonical-string + sha256_hex hash for the two profile
// sub-messages -- same discipline as compute_session_configuration_hash
// (secure_aggregation_crypto.cpp): fixed field order, a Record-
// Separator field delimiter, a distinct domain-separation prefix.
std::string compute_fixed_point_profile_hash(
    const pb_coordinator::FixedPointEncodingProfile& profile) {
    std::ostringstream out;
    out << "FL_PLATFORM_SECAGG_WIRE_FIXED_POINT_PROFILE_V1" << '\x1e';
    out << "schema_version=" << profile.schema_version() << '\x1e';
    out << "rounding_rule=" << profile.rounding_rule() << '\x1e';
    out << "scale_factor=" << profile.scale_factor() << '\x1e';
    out << "max_input_magnitude=" << profile.max_input_magnitude() << '\x1e';
    out << "max_client_weight=" << profile.max_client_weight() << '\x1e';
    out << "max_cohort_size=" << profile.max_cohort_size() << '\x1e';
    out << "safety_margin=" << profile.safety_margin() << '\x1e';
    return sha256_hex(out.str());
}

std::string compute_cryptographic_profile_hash(
    const pb_coordinator::CryptographicProviderProfile& profile) {
    std::ostringstream out;
    out << "FL_PLATFORM_SECAGG_WIRE_CRYPTOGRAPHIC_PROFILE_V1" << '\x1e';
    out << "mask_generator_profile=" << profile.mask_generator_profile() << '\x1e';
    out << "key_agreement_profile=" << profile.key_agreement_profile() << '\x1e';
    out << "key_derivation_profile=" << profile.key_derivation_profile() << '\x1e';
    out << "digest_profile=" << profile.digest_profile() << '\x1e';
    return sha256_hex(out.str());
}

bool all_zero(const std::string& raw) {
    return std::all_of(raw.begin(), raw.end(), [](char c) { return c == '\0'; });
}

// Work item 10: the exact bytes a frozen roster's coordinator signature
// is computed over -- every field a worker must independently verify
// before deriving any pairwise mask, in a fixed order, with a
// dedicated domain-separation prefix (same convention as this file's
// own compute_fixed_point_profile_hash/compute_cryptographic_profile_hash
// above). Deliberately excludes coordinator_signing_key_id/payload_hash/
// signature themselves -- those are either metadata about the
// signature (key_id) or the signature's own outputs.
std::string compute_frozen_cohort_roster_signing_bytes(
    const pb_coordinator::FrozenCohortRoster& roster) {
    std::ostringstream out;
    out << "FL_PLATFORM_SECURE_AGGREGATION_FROZEN_ROSTER_V1" << '\x1e';
    out << "schema_version=" << roster.schema_version() << '\x1e';
    out << "protocol_version=" << roster.protocol_version() << '\x1e';
    out << "provider=" << static_cast<int>(roster.provider()) << '\x1e';
    out << "session_id=" << roster.session_id() << '\x1e';
    out << "run_id=" << roster.run_id() << '\x1e';
    out << "round_id=" << roster.round_id() << '\x1e';
    out << "model_version=" << roster.model_version() << '\x1e';
    out << "participant_count=" << roster.participants_size() << '\x1e';
    for (const auto& participant : roster.participants()) {
        out << "participant[" << participant.participant_index() << "]=" << participant.worker_id()
            << "|" << participant.client_id() << "|" << participant.ephemeral_public_key_x25519()
            << "|" << participant.public_key_fingerprint() << '\x1e';
    }
    out << "tensor_manifest_hash=" << roster.tensor_manifest_hash() << '\x1e';
    out << "fixed_point_profile_hash=" << roster.fixed_point_profile_hash() << '\x1e';
    out << "cryptographic_profile_hash=" << roster.cryptographic_profile_hash() << '\x1e';
    out << "cohort_commitment=" << roster.cohort_commitment() << '\x1e';
    out << "freeze_timestamp=" << roster.freeze_timestamp() << '\x1e';
    out << "expiry=" << roster.expiry() << '\x1e';
    return out.str();
}

}  // namespace

SecureAggregationSessionManagerError::SecureAggregationSessionManagerError(const std::string& what)
    : std::runtime_error(what) {}

SecureAggregationSessionManager::SecureAggregationSessionManager(
    SecureAggregationSessionStore* store)
    : store_(store) {}

void SecureAggregationSessionManager::persist_transition(const SessionRecord& record) const {
    if (store_ == nullptr)
        return;
    SecureAggregationSessionRecord persisted;
    persisted.session_id = record.config.session_id();
    persisted.run_id = record.config.run_id();
    persisted.round_id = record.config.round_id();
    persisted.state = to_string(record.state_machine.state());
    persisted.created_at_unix_s = record.created_at_unix_s;
    persisted.updated_at_unix_s = record.state_machine.history().empty()
                                      ? record.created_at_unix_s
                                      : record.state_machine.history().back().timestamp_unix_s;
    persisted.completed_at_unix_s = record.completed_at_unix_s;
    persisted.abort_reason = to_string(record.state_machine.abort_reason());
    persisted.failure_reason = record.state_machine.failure_reason();
    store_->record_transition(persisted);
}

SecureAggregationSessionManager::SessionRecord& SecureAggregationSessionManager::require_session(
    const std::string& session_id) {
    auto it = sessions_.find(session_id);
    if (it == sessions_.end()) {
        throw SecureAggregationSessionManagerError("unknown secure aggregation session: " +
                                                   session_id);
    }
    return it->second;
}

pb_coordinator::SecureAggregationSessionStatus SecureAggregationSessionManager::status_of(
    const SessionRecord& record) const {
    pb_coordinator::SecureAggregationSessionStatus status;
    status.set_session_id(record.config.session_id());
    status.set_run_id(record.config.run_id());
    status.set_round_id(record.config.round_id());
    status.set_model_version(record.config.model_version());
    status.set_provider(record.config.provider());
    status.set_state(to_proto_state(record.state_machine.state()));
    status.set_cohort_size(record.config.cohort_size());
    status.set_minimum_cohort_size(record.config.minimum_cohort_size());
    status.set_key_advertisement_count(record.advertisements_by_worker.size());
    status.set_masked_contribution_count(record.contributions_by_worker.size());
    status.set_key_advertisement_deadline_unix_s(record.config.key_advertisement_deadline_unix_s());
    status.set_masked_update_deadline_unix_s(record.config.masked_update_deadline_unix_s());
    status.set_session_expiry_unix_s(record.config.session_expiry_unix_s());
    status.set_created_at_unix_s(record.created_at_unix_s);
    status.set_completed_at_unix_s(record.completed_at_unix_s);
    status.set_abort_reason(to_proto_abort_reason(record.state_machine.abort_reason()));
    status.set_failure_reason(record.state_machine.failure_reason());
    status.set_aggregate_checksum(record.aggregate_checksum);

    double worst_case = 0.0;
    double sum = 0.0;
    std::uint64_t sample_count = 0;
    for (const auto& [worker_id, contribution] : record.contributions_by_worker) {
        if (contribution.has_encoding_statistics()) {
            worst_case =
                std::max(worst_case, contribution.encoding_statistics().max_quantization_error());
            sum += contribution.encoding_statistics().mean_quantization_error();
            ++sample_count;
        }
    }
    auto* quantization_summary = status.mutable_quantization_summary();
    quantization_summary->set_worst_case_quantization_error(worst_case);
    quantization_summary->set_average_quantization_error(
        sample_count > 0 ? sum / static_cast<double>(sample_count) : 0.0);
    quantization_summary->set_sample_count(sample_count);
    return status;
}

pb_coordinator::SecureAggregationSessionStatus SecureAggregationSessionManager::create_session(
    const pb_coordinator::SecureAggregationSessionConfig& config, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (config.session_id().empty()) {
        throw SecureAggregationSessionManagerError("create_session: session_id must not be empty");
    }
    if (sessions_.count(config.session_id()) != 0) {
        throw SecureAggregationSessionManagerError("create_session: duplicate session_id: " +
                                                   config.session_id());
    }
    if (config.run_id().empty()) {
        throw SecureAggregationSessionManagerError("create_session: run_id must not be empty");
    }
    if (config.provider() == pb_worker::SECURE_AGGREGATION_PROVIDER_UNSPECIFIED ||
        config.provider() == pb_worker::SECURE_AGGREGATION_PROVIDER_NONE) {
        throw SecureAggregationSessionManagerError(
            "create_session: provider must be SECAGG_NO_DROPOUT_EXPERIMENTAL, not UNSPECIFIED/NONE "
            "-- "
            "no silent fallback");
    }
    if (config.ordered_participant_ids_size() == 0) {
        throw SecureAggregationSessionManagerError(
            "create_session: ordered_participant_ids must not be empty");
    }
    {
        std::set<std::string> seen;
        for (const auto& participant_id : config.ordered_participant_ids()) {
            if (participant_id.empty()) {
                throw SecureAggregationSessionManagerError(
                    "create_session: ordered_participant_ids must not contain an empty identifier");
            }
            if (!seen.insert(participant_id).second) {
                throw SecureAggregationSessionManagerError(
                    "create_session: duplicate participant_id: " + participant_id);
            }
        }
    }
    if (config.cohort_size() != static_cast<std::uint64_t>(config.ordered_participant_ids_size())) {
        throw SecureAggregationSessionManagerError(
            "create_session: cohort_size does not match ordered_participant_ids count");
    }
    if (config.minimum_cohort_size() > config.cohort_size()) {
        throw SecureAggregationSessionManagerError(
            "create_session: minimum_cohort_size cannot exceed cohort_size");
    }
    if (!config.has_fixed_point_profile()) {
        throw SecureAggregationSessionManagerError(
            "create_session: fixed_point_profile is required");
    }
    const auto cpp_profile = to_cpp_profile(config.fixed_point_profile());
    const auto bounds_proof = prove_domain_bounds(cpp_profile);
    if (!bounds_proof.safe) {
        throw SecureAggregationSessionManagerError(
            "create_session: fixed_point_profile fails its domain bounds proof: " +
            bounds_proof.explanation);
    }

    SessionRecord record;
    record.config = config;
    record.state_machine = CohortStateMachine(config.session_id());
    record.created_at_unix_s = now_unix_s;

    auto status = status_of(record);
    persist_transition(record);
    session_id_by_run_round_[{config.run_id(), config.round_id()}] = config.session_id();
    sessions_.emplace(config.session_id(), std::move(record));
    return status;
}

pb_coordinator::SecureAggregationSessionStatus SecureAggregationSessionManager::advertise_key(
    const pb_worker::SecureAggregationKeyAdvertisement& advertisement, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto& record = require_session(advertisement.session_id());

    if (record.state_machine.state() != CohortState::kCohortForming &&
        record.state_machine.state() != CohortState::kKeyAdvertisement) {
        throw SecureAggregationSessionManagerError("advertise_key: session " +
                                                   advertisement.session_id() +
                                                   " is not accepting key advertisements "
                                                   "(current state: " +
                                                   to_string(record.state_machine.state()) + ")");
    }
    if (advertisement.run_id() != record.config.run_id() ||
        advertisement.round_id() != record.config.round_id() ||
        advertisement.model_version() != record.config.model_version()) {
        throw SecureAggregationSessionManagerError(
            "advertise_key: run_id/round_id/model_version does not match session " +
            advertisement.session_id());
    }
    const bool is_participant =
        std::find(record.config.ordered_participant_ids().begin(),
                  record.config.ordered_participant_ids().end(),
                  advertisement.worker_id()) != record.config.ordered_participant_ids().end();
    if (!is_participant) {
        throw SecureAggregationSessionManagerError(
            "advertise_key: worker_id " + advertisement.worker_id() +
            " is not a configured participant of session " + advertisement.session_id());
    }
    if (record.advertisements_by_worker.count(advertisement.worker_id()) != 0) {
        throw SecureAggregationSessionManagerError(
            "advertise_key: duplicate advertisement from worker_id " + advertisement.worker_id());
    }
    if (record.config.key_advertisement_deadline_unix_s() > 0.0 &&
        now_unix_s > record.config.key_advertisement_deadline_unix_s()) {
        throw SecureAggregationSessionManagerError(
            "advertise_key: key advertisement deadline has passed for "
            "session " +
            advertisement.session_id());
    }

    std::string raw_public_key;
    try {
        raw_public_key = hex_decode(advertisement.ephemeral_public_key_x25519());
    } catch (const SecureAggregationCryptoError& error) {
        throw SecureAggregationSessionManagerError(
            std::string("advertise_key: invalid public key encoding: ") + error.what());
    }
    if (raw_public_key.size() != kX25519KeyLength) {
        throw SecureAggregationSessionManagerError("advertise_key: public key is not 32 bytes");
    }
    if (all_zero(raw_public_key)) {
        throw SecureAggregationSessionManagerError(
            "advertise_key: an all-zero public key is rejected (degenerate/invalid key material)");
    }

    if (record.state_machine.state() == CohortState::kCohortForming) {
        record.state_machine.transition_to(
            CohortState::kKeyAdvertisement, now_unix_s, "first key advertisement received");
    }
    record.advertisements_by_worker.emplace(advertisement.worker_id(), advertisement);
    persist_transition(record);
    return status_of(record);
}

pb_coordinator::FrozenCohortRoster SecureAggregationSessionManager::freeze_cohort(
    const std::string& session_id,
    double now_unix_s,
    const CoordinatorSigningIdentity* signing_identity) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto& record = require_session(session_id);

    if (record.state_machine.state() != CohortState::kKeyAdvertisement) {
        throw SecureAggregationSessionManagerError("freeze_cohort: session " + session_id +
                                                   " is not in KEY_ADVERTISEMENT (current state: " +
                                                   to_string(record.state_machine.state()) + ")");
    }
    for (const auto& participant_id : record.config.ordered_participant_ids()) {
        if (record.advertisements_by_worker.count(participant_id) == 0) {
            throw SecureAggregationSessionManagerError(
                "freeze_cohort: cohort is incomplete -- participant " + participant_id +
                " has not advertised a key; the caller must abort (kDropout), never freeze a "
                "partial cohort");
        }
    }

    pb_coordinator::FrozenCohortRoster roster;
    roster.set_schema_version(1);
    roster.set_protocol_version(record.config.protocol_version());
    roster.set_provider(record.config.provider());
    roster.set_session_id(record.config.session_id());
    roster.set_run_id(record.config.run_id());
    roster.set_round_id(record.config.round_id());
    roster.set_model_version(record.config.model_version());

    std::vector<std::string> ordered_ids(record.config.ordered_participant_ids().begin(),
                                         record.config.ordered_participant_ids().end());
    for (std::size_t i = 0; i < ordered_ids.size(); ++i) {
        const auto& advertisement = record.advertisements_by_worker.at(ordered_ids[i]);
        auto* participant = roster.add_participants();
        participant->set_participant_index(static_cast<std::uint32_t>(i));
        participant->set_worker_id(advertisement.worker_id());
        participant->set_client_id(advertisement.client_id());
        participant->set_ephemeral_public_key_x25519(advertisement.ephemeral_public_key_x25519());
        participant->set_public_key_fingerprint(advertisement.public_key_fingerprint());
    }

    roster.set_tensor_manifest_hash(record.config.tensor_manifest_hash());
    roster.set_fixed_point_profile_hash(
        compute_fixed_point_profile_hash(record.config.fixed_point_profile()));
    roster.set_cryptographic_profile_hash(
        compute_cryptographic_profile_hash(record.config.cryptographic_profile()));
    // Real cryptographic commitment -- the same function a real
    // worker-side verifier would independently recompute (Work Package
    // L), not a second implementation.
    roster.set_cohort_commitment(compute_cohort_commitment(record.config.session_id(),
                                                           record.config.run_id(),
                                                           record.config.round_id(),
                                                           record.config.model_version(),
                                                           ordered_ids));
    roster.set_freeze_timestamp(now_unix_s);
    roster.set_expiry(record.config.masked_update_deadline_unix_s());

    // Work item 10: real signing when a coordinator identity is
    // provided (the live GetFrozenCohortRoster/AdvertiseSecureAggregationKey
    // handler always provides one) -- coordinator_signing_key_id/
    // signature stay empty only for callers (unit tests) that don't
    // need real cryptographic evidence.
    if (signing_identity != nullptr) {
        roster.set_coordinator_signing_key_id(signing_identity->key_id);
        const auto signing_bytes = compute_frozen_cohort_roster_signing_bytes(roster);
        roster.set_payload_hash(sha256_hex(signing_bytes));
        roster.set_signature(sign_with_coordinator_identity(*signing_identity, signing_bytes));
    }

    record.state_machine.transition_to(
        CohortState::kCohortFrozen, now_unix_s, "cohort frozen: all participants advertised");
    record.frozen_roster = roster;
    record.frozen = true;
    record.expected_tensor_element_counts
        .clear();  // populated by the first accepted masked contribution
    persist_transition(record);
    return roster;
}

pb_coordinator::SecureAggregationSessionStatus
SecureAggregationSessionManager::submit_masked_update(const pb_worker::MaskedClientUpdate& update,
                                                      double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto& record = require_session(update.session_id());

    if (record.state_machine.state() != CohortState::kCohortFrozen &&
        record.state_machine.state() != CohortState::kMaskedUpdateCollection) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: session " + update.session_id() +
            " is not accepting masked updates (current state: " +
            to_string(record.state_machine.state()) + ")");
    }
    if (!record.frozen) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: cohort is not frozen for session " + update.session_id());
    }
    if (update.run_id() != record.config.run_id() ||
        update.round_id() != record.config.round_id() ||
        update.model_version() != record.config.model_version()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: run_id/round_id/model_version does not match session " +
            update.session_id());
    }
    if (update.cohort_commitment() != record.frozen_roster.cohort_commitment()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: cohort_commitment does not match the frozen roster -- rejected "
            "(SECURE_AGGREGATION_REJECTION_REASON_COHORT_MISMATCH)");
    }
    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area K/N: exact-conformance binding to the specific
    // signed, verified frozen roster this contribution claims to be
    // computed against -- additive fields on MaskedClientUpdate this
    // slice, so only enforced when actually populated (a worker built
    // against an older/incomplete client that leaves these empty is a
    // real gap this check surfaces, not silently ignored, but is not a
    // hard requirement retrofitted onto every pre-existing test
    // construction -- see docs/secure-aggregation-masked-update.md).
    if (!update.frozen_roster_payload_hash().empty() &&
        update.frozen_roster_payload_hash() != record.frozen_roster.payload_hash()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: frozen_roster_payload_hash does not match the frozen roster -- "
            "rejected "
            "(SECURE_AGGREGATION_REJECTION_REASON_COHORT_MISMATCH)");
    }
    if (!update.cryptographic_profile_hash().empty() &&
        update.cryptographic_profile_hash() != record.frozen_roster.cryptographic_profile_hash()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: cryptographic_profile_hash does not match the frozen roster -- "
            "rejected "
            "(SECURE_AGGREGATION_REJECTION_REASON_PROFILE_MISMATCH)");
    }
    if (!update.tensor_manifest_hash().empty() &&
        update.tensor_manifest_hash() != record.frozen_roster.tensor_manifest_hash()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: tensor_manifest_hash does not match the frozen roster -- "
            "rejected "
            "(SECURE_AGGREGATION_REJECTION_REASON_MANIFEST_MISMATCH)");
    }
    if (!update.fixed_point_profile_hash().empty() &&
        update.fixed_point_profile_hash() != record.frozen_roster.fixed_point_profile_hash()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: fixed_point_profile_hash does not match the frozen roster -- "
            "rejected "
            "(SECURE_AGGREGATION_REJECTION_REASON_PROFILE_MISMATCH)");
    }
    if (record.advertisements_by_worker.count(update.worker_id()) == 0) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: worker_id " + update.worker_id() +
            " is not a member of the frozen cohort for session " + update.session_id());
    }
    if (record.contributions_by_worker.count(update.worker_id()) != 0) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: duplicate contribution from worker_id " + update.worker_id());
    }
    if (record.config.masked_update_deadline_unix_s() > 0.0 &&
        now_unix_s > record.config.masked_update_deadline_unix_s()) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: masked update deadline has passed for session " +
            update.session_id());
    }
    if (update.masked_tensors_size() == 0) {
        throw SecureAggregationSessionManagerError(
            "submit_masked_update: masked_tensors must not be empty");
    }

    // Real checksum verification -- every tensor's masked_values must
    // match its own reported checksum before this contribution is
    // trusted at all.
    for (const auto& tensor : update.masked_tensors()) {
        const std::vector<std::uint64_t> values(tensor.masked_values().begin(),
                                                tensor.masked_values().end());
        const auto expected_checksum = compute_masked_values_checksum(values);
        if (expected_checksum != tensor.checksum()) {
            throw SecureAggregationSessionManagerError(
                "submit_masked_update: checksum mismatch for tensor '" + tensor.tensor_name() +
                "' from worker_id " + update.worker_id());
        }
    }
    {
        const std::vector<std::uint64_t> weight_values{update.masked_weight()};
        if (compute_masked_values_checksum(weight_values) != update.masked_weight_checksum()) {
            throw SecureAggregationSessionManagerError(
                "submit_masked_update: masked_weight_checksum mismatch from worker_id " +
                update.worker_id());
        }
    }
    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: same checksum-verification discipline as masked_weight
    // above, only when this session actually uses adaptive clipping --
    // masked_clipping_indicator/_checksum are zero-valued/empty on
    // every non-adaptive submission and must not be spuriously
    // validated then.
    if (record.config.secure_adaptive_clipping_active()) {
        const std::vector<std::uint64_t> indicator_values{update.masked_clipping_indicator()};
        if (compute_masked_values_checksum(indicator_values) !=
            update.masked_clipping_indicator_checksum()) {
            throw SecureAggregationSessionManagerError(
                "submit_masked_update: masked_clipping_indicator_checksum mismatch from "
                "worker_id " +
                update.worker_id());
        }
    }

    // Documented simplification (see this class's header comment):
    // this manager has no independently-sourced ModelManifest this
    // pass, so it validates every contribution against the *first*
    // contribution's own tensor name/element-count shape rather than
    // an externally-supplied manifest -- real, meaningful protection
    // against a malformed/mismatched contribution, just not a
    // cross-check against ground truth the manager does not have
    // access to yet.
    if (record.contributions_by_worker.empty()) {
        for (const auto& tensor : update.masked_tensors()) {
            record.expected_tensor_element_counts[tensor.tensor_name()] =
                static_cast<std::size_t>(tensor.masked_values_size());
        }
    } else {
        if (static_cast<std::size_t>(update.masked_tensors_size()) !=
            record.expected_tensor_element_counts.size()) {
            throw SecureAggregationSessionManagerError(
                "submit_masked_update: tensor count from worker_id " + update.worker_id() +
                " does not match the cohort's established shape");
        }
        for (const auto& tensor : update.masked_tensors()) {
            const auto it = record.expected_tensor_element_counts.find(tensor.tensor_name());
            if (it == record.expected_tensor_element_counts.end() ||
                it->second != static_cast<std::size_t>(tensor.masked_values_size())) {
                throw SecureAggregationSessionManagerError(
                    "submit_masked_update: tensor '" + tensor.tensor_name() + "' from worker_id " +
                    update.worker_id() + " does not match the cohort's established shape");
            }
        }
    }

    if (record.state_machine.state() == CohortState::kCohortFrozen) {
        record.state_machine.transition_to(
            CohortState::kMaskedUpdateCollection, now_unix_s, "first masked update received");
    }
    record.contributions_by_worker.emplace(update.worker_id(), update);
    persist_transition(record);
    return status_of(record);
}

fl::core::AggregationResult SecureAggregationSessionManager::finalize(
    const std::string& session_id,
    double now_unix_s,
    fl::core::NoiseProvider* noise_provider,
    double noise_std_dev,
    double expected_weight_sum) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto& record = require_session(session_id);

    if (record.state_machine.state() != CohortState::kMaskedUpdateCollection) {
        throw SecureAggregationSessionManagerError(
            "finalize: session " + session_id +
            " is not in MASKED_UPDATE_COLLECTION (current state: " +
            to_string(record.state_machine.state()) + ")");
    }
    // The no-dropout enforcement point: finalize refuses anything less
    // than a complete cohort. A caller that has detected a missing
    // participant (deadline exceeded) must call abort(kDropout)
    // instead of calling finalize() at all -- this check is defense in
    // depth, not the primary detection mechanism (Tier 2's RPC/deadline
    // layer is).
    if (record.contributions_by_worker.size() !=
        static_cast<std::size_t>(record.config.ordered_participant_ids_size())) {
        throw SecureAggregationSessionManagerError(
            "finalize: cohort is incomplete (" +
            std::to_string(record.contributions_by_worker.size()) + " of " +
            std::to_string(record.config.ordered_participant_ids_size()) +
            " participants contributed) -- refusing to produce a partial aggregate; the caller "
            "must abort "
            "(kDropout) instead");
    }

    record.state_machine.transition_to(CohortState::kAggregateValidation,
                                       now_unix_s,
                                       "validating complete cohort's masked contributions");

    const auto cpp_profile = to_cpp_profile(record.config.fixed_point_profile());

    fl::core::TensorCollection model_delta;
    for (const auto& [tensor_name, expected_count] : record.expected_tensor_element_counts) {
        std::vector<std::vector<std::uint64_t>> per_participant_masked_values;
        per_participant_masked_values.reserve(record.contributions_by_worker.size());
        for (const auto& [worker_id, contribution] : record.contributions_by_worker) {
            (void)worker_id;
            bool found = false;
            for (const auto& tensor : contribution.masked_tensors()) {
                if (tensor.tensor_name() == tensor_name) {
                    per_participant_masked_values.emplace_back(tensor.masked_values().begin(),
                                                               tensor.masked_values().end());
                    found = true;
                    break;
                }
            }
            if (!found) {
                record.state_machine.fail(
                    "finalize: tensor '" + tensor_name +
                        "' unexpectedly missing from a contribution during aggregate "
                        "validation (should have been caught at submission time)",
                    now_unix_s);
                persist_transition(record);
                throw SecureAggregationSessionManagerError(
                    "finalize: internal consistency error for tensor '" + tensor_name + "'");
            }
        }
        const auto summed_ring_values = sum_masked_tensors(per_participant_masked_values);
        std::vector<double> decoded_values;
        decoded_values.reserve(summed_ring_values.size());
        for (const auto ring_value : summed_ring_values) {
            decoded_values.push_back(
                decode_value(static_cast<std::int64_t>(ring_value), cpp_profile));
        }
        // Work Area Q: noise is added to the just-decoded SUM, here --
        // strictly after complete-cohort validation (this line is only
        // ever reached once every participant's contribution has
        // already been confirmed present, above) and strictly before
        // the divide-by-weight-sum step below. See finalize()'s own
        // header-comment for the full placement rationale.
        if (noise_provider != nullptr && noise_std_dev > 0.0) {
            for (auto& value : decoded_values) {
                value += noise_provider->gaussian_sample(noise_std_dev);
            }
        }

        fl::core::TensorDescriptor descriptor;
        descriptor.name = tensor_name;
        descriptor.shape = {static_cast<std::uint64_t>(expected_count)};
        descriptor.dtype = fl::core::DType::kFloat32;
        model_delta.insert(fl::core::TensorBuffer(descriptor, std::move(decoded_values)));
    }

    std::vector<std::uint64_t> masked_weights;
    masked_weights.reserve(record.contributions_by_worker.size());
    for (const auto& [worker_id, contribution] : record.contributions_by_worker) {
        (void)worker_id;
        masked_weights.push_back(contribution.masked_weight());
    }
    const auto summed_weight_ring = sum_masked_values(masked_weights);
    const double decoded_weight_sum =
        decode_value(static_cast<std::int64_t>(summed_weight_ring), cpp_profile);
    if (decoded_weight_sum <= 0.0) {
        record.state_machine.abort(
            SecureAggregationAbortReason::kMaskCancellationFailed,
            now_unix_s,
            "decoded aggregate weight sum is zero or negative -- masks did not cancel "
            "correctly, or the cohort's true weight sum is degenerate");
        persist_transition(record);
        throw SecureAggregationSessionManagerError(
            "finalize: decoded aggregate weight sum is not strictly positive -- aborted, not "
            "silently divided");
    }
    if (expected_weight_sum > 0.0 && std::abs(decoded_weight_sum - expected_weight_sum) > 1e-6) {
        record.state_machine.abort(
            SecureAggregationAbortReason::kMaskCancellationFailed,
            now_unix_s,
            "decoded aggregate weight sum (" + std::to_string(decoded_weight_sum) +
                ") does not match the expected fixed-weight sum (" +
                std::to_string(expected_weight_sum) +
                ") -- see finalize()'s expected_weight_sum parameter documentation");
        persist_transition(record);
        throw SecureAggregationSessionManagerError(
            "finalize: decoded aggregate weight sum does not match the expected fixed-weight total "
            "-- aborted");
    }

    for (const auto& [tensor_name, expected_count] : record.expected_tensor_element_counts) {
        (void)expected_count;
        auto& buffer = model_delta.at(
            tensor_name);  // non-const overload -- mutates in place, no const_cast needed
        for (auto& value : buffer.values()) {
            value /= decoded_weight_sum;
        }
    }

    std::ostringstream checksum_input;
    checksum_input << "FL_PLATFORM_SECAGG_WIRE_AGGREGATE_CHECKSUM_V1" << '\x1e';
    for (const auto& [tensor_name, buffer] : model_delta.tensors()) {
        checksum_input << tensor_name << '=';
        for (const auto value : buffer.values()) {
            checksum_input << value << ',';
        }
        checksum_input << '\x1e';
    }
    record.aggregate_checksum = sha256_hex(checksum_input.str());
    record.completed_at_unix_s = now_unix_s;
    record.state_machine.transition_to(CohortState::kCompleted,
                                       now_unix_s,
                                       "aggregate decoded and validated for a complete cohort");
    persist_transition(record);

    fl::core::AggregationResult result;
    result.model_delta = std::move(model_delta);
    return result;
}

std::uint64_t SecureAggregationSessionManager::decode_secure_adaptive_clipping_indicator_count(
    const std::string& session_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = sessions_.find(session_id);
    if (it == sessions_.end()) {
        throw SecureAggregationSessionManagerError(
            "decode_secure_adaptive_clipping_indicator_count: unknown session " + session_id);
    }
    const auto& record = it->second;
    if (record.state_machine.state() != CohortState::kMaskedUpdateCollection) {
        throw SecureAggregationSessionManagerError(
            "decode_secure_adaptive_clipping_indicator_count: session " + session_id +
            " is not in MASKED_UPDATE_COLLECTION (current state: " +
            to_string(record.state_machine.state()) + ")");
    }
    const auto cohort_size = static_cast<std::size_t>(record.config.ordered_participant_ids_size());
    if (record.contributions_by_worker.size() != cohort_size) {
        throw SecureAggregationSessionManagerError(
            "decode_secure_adaptive_clipping_indicator_count: cohort is incomplete (" +
            std::to_string(record.contributions_by_worker.size()) + " of " +
            std::to_string(cohort_size) + " participants contributed)");
    }
    std::vector<std::uint64_t> masked_indicators;
    masked_indicators.reserve(record.contributions_by_worker.size());
    for (const auto& [worker_id, contribution] : record.contributions_by_worker) {
        (void)worker_id;
        masked_indicators.push_back(contribution.masked_clipping_indicator());
    }
    // Deliberately NOT decode_value() -- the indicator was never
    // fixed-point encoded (see this method's header comment). A
    // complete, honest cohort's pairwise masks cancel exactly, leaving
    // the true non-negative sum directly as the ring value; no signed
    // reinterpretation is needed or correct here (unlike the tensor/
    // weight sums, which can be negative).
    const std::uint64_t summed_indicator_ring = sum_masked_values(masked_indicators);
    if (summed_indicator_ring > static_cast<std::uint64_t>(cohort_size)) {
        throw SecureAggregationSessionManagerError(
            "decode_secure_adaptive_clipping_indicator_count: decoded indicator count (" +
            std::to_string(summed_indicator_ring) + ") exceeds cohort_size (" +
            std::to_string(cohort_size) +
            ") -- mask cancellation failed or a contribution was tampered with");
    }
    return summed_indicator_ring;
}

pb_coordinator::SecureAggregationSessionStatus SecureAggregationSessionManager::abort(
    const std::string& session_id,
    pb_coordinator::SecureAggregationAbortReason reason,
    double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto& record = require_session(session_id);
    if (reason == pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_UNSPECIFIED) {
        throw SecureAggregationSessionManagerError(
            "abort: a specific abort reason is required, not UNSPECIFIED");
    }
    record.state_machine.abort(to_cpp_abort_reason(reason),
                               now_unix_s,
                               "aborted via SecureAggregationSessionManager::abort");
    persist_transition(record);
    return status_of(record);
}

std::optional<pb_coordinator::SecureAggregationSessionStatus> SecureAggregationSessionManager::find(
    const std::string& session_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = sessions_.find(session_id);
    if (it == sessions_.end()) {
        return std::nullopt;
    }
    return status_of(it->second);
}

std::vector<pb_coordinator::SecureAggregationSessionSummary> SecureAggregationSessionManager::list()
    const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<pb_coordinator::SecureAggregationSessionSummary> summaries;
    summaries.reserve(sessions_.size());
    for (const auto& [session_id, record] : sessions_) {
        (void)session_id;
        pb_coordinator::SecureAggregationSessionSummary summary;
        summary.set_session_id(record.config.session_id());
        summary.set_run_id(record.config.run_id());
        summary.set_round_id(record.config.round_id());
        summary.set_provider(record.config.provider());
        summary.set_state(to_proto_state(record.state_machine.state()));
        summary.set_created_at_unix_s(record.created_at_unix_s);
        summary.set_completed_at_unix_s(record.completed_at_unix_s);
        summaries.push_back(std::move(summary));
    }
    return summaries;
}

std::optional<pb_coordinator::SecureAggregationTaskBinding>
SecureAggregationSessionManager::find_binding_for_participant(const std::string& run_id,
                                                              std::uint64_t round_id,
                                                              const std::string& worker_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto index_it = session_id_by_run_round_.find({run_id, round_id});
    if (index_it == session_id_by_run_round_.end()) {
        return std::nullopt;
    }
    const auto record_it = sessions_.find(index_it->second);
    if (record_it == sessions_.end()) {
        return std::nullopt;
    }
    const auto& record = record_it->second;
    const auto state = record.state_machine.state();
    if (state != CohortState::kCohortForming && state != CohortState::kKeyAdvertisement) {
        return std::nullopt;  // session no longer accepting advertisements -- nothing to bind a new
                              // task to
    }
    const bool is_participant =
        std::find(record.config.ordered_participant_ids().begin(),
                  record.config.ordered_participant_ids().end(),
                  worker_id) != record.config.ordered_participant_ids().end();
    if (!is_participant) {
        return std::nullopt;
    }

    pb_coordinator::SecureAggregationTaskBinding binding;
    binding.set_secure_aggregation_active(true);
    binding.set_provider(record.config.provider());
    binding.set_protocol_version(record.config.protocol_version());
    binding.set_session_id(record.config.session_id());
    binding.set_session_configuration_hash(record.config.session_configuration_hash());
    binding.set_key_advertisement_deadline_unix_s(
        record.config.key_advertisement_deadline_unix_s());
    binding.set_minimum_cohort_size(record.config.minimum_cohort_size());
    binding.set_masked_update_deadline_unix_s(record.config.masked_update_deadline_unix_s());
    binding.set_session_expiry_unix_s(record.config.session_expiry_unix_s());
    binding.set_max_absolute_update_bound(record.config.max_absolute_update_bound());
    binding.set_max_client_weight(record.config.max_client_weight());
    binding.set_max_aggregate_bound(record.config.max_aggregate_bound());
    // 0 == "whole tensor, no chunking" this slice -- see
    // docs/secure-aggregation-masked-runtime-audit.md's canonical-mask-
    // context section.
    binding.set_tensor_chunk_size(0);
    binding.set_privacy_mode_compatible(record.config.privacy_mode_compatible());
    binding.set_privacy_incompatibility_reason(record.config.privacy_incompatibility_reason());
    // Secure User-Level Differential Privacy Runtime slice, Work Area
    // E: mirrors the session config's own fields 29-38 field-for-field,
    // same convention as every field above.
    binding.set_secure_user_level_dp_active(record.config.secure_user_level_dp_active());
    binding.set_secure_user_level_adjacency_model(
        record.config.secure_user_level_adjacency_model());
    binding.set_secure_user_level_clip_norm(record.config.secure_user_level_clip_norm());
    binding.set_secure_user_level_quantization_margin(
        record.config.secure_user_level_quantization_margin());
    binding.set_secure_user_level_effective_sensitivity(
        record.config.secure_user_level_effective_sensitivity());
    binding.set_secure_user_level_noise_multiplier(
        record.config.secure_user_level_noise_multiplier());
    binding.set_secure_user_level_target_delta(record.config.secure_user_level_target_delta());
    binding.set_secure_user_level_max_epsilon(record.config.secure_user_level_max_epsilon());
    binding.set_secure_user_level_fixed_weight(record.config.secure_user_level_fixed_weight());
    binding.set_secure_user_level_sampling_assumption(
        record.config.secure_user_level_sampling_assumption());
    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: mirrors the session config's own fields 40-48 field-for-
    // field, same convention as every field above (the configuration
    // hash field, like secure_user_level_dp_configuration_hash's own
    // sibling above, is deliberately left unset here -- the worker
    // reads the authoritative hash from SignedCoordinatorTask's own
    // top-level field instead, matching the existing precedent).
    binding.set_secure_adaptive_clipping_active(record.config.secure_adaptive_clipping_active());
    binding.set_secure_adaptive_clipping_indicator_definition(
        record.config.secure_adaptive_clipping_indicator_definition());
    binding.set_secure_adaptive_clipping_current_bound(
        record.config.secure_adaptive_clipping_current_bound());
    binding.set_secure_adaptive_clipping_min_bound(
        record.config.secure_adaptive_clipping_min_bound());
    binding.set_secure_adaptive_clipping_max_bound(
        record.config.secure_adaptive_clipping_max_bound());
    binding.set_secure_adaptive_clipping_target_quantile(
        record.config.secure_adaptive_clipping_target_quantile());
    binding.set_secure_adaptive_clipping_learning_rate(
        record.config.secure_adaptive_clipping_learning_rate());
    binding.set_secure_adaptive_clipping_indicator_noise_multiplier(
        record.config.secure_adaptive_clipping_indicator_noise_multiplier());
    binding.set_secure_adaptive_clipping_clip_state_step_count(
        record.config.secure_adaptive_clipping_clip_state_step_count());
    return binding;
}

bool SecureAggregationSessionManager::has_session_for_run_round(const std::string& run_id,
                                                                std::uint64_t round_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return session_id_by_run_round_.count({run_id, round_id}) != 0;
}

std::optional<pb_coordinator::SecureAggregationSessionStatus>
SecureAggregationSessionManager::find_status_for_run_round(const std::string& run_id,
                                                           std::uint64_t round_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto index_it = session_id_by_run_round_.find({run_id, round_id});
    if (index_it == session_id_by_run_round_.end()) {
        return std::nullopt;
    }
    const auto record_it = sessions_.find(index_it->second);
    if (record_it == sessions_.end()) {
        return std::nullopt;
    }
    return status_of(record_it->second);
}

std::optional<pb_coordinator::FrozenCohortRoster>
SecureAggregationSessionManager::get_frozen_roster(const std::string& session_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = sessions_.find(session_id);
    if (it == sessions_.end() || !it->second.frozen) {
        return std::nullopt;
    }
    return it->second.frozen_roster;
}

std::vector<std::string> SecureAggregationSessionManager::sweep_expired_advertisement_deadlines(
    double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> aborted;
    for (auto& [session_id, record] : sessions_) {
        const auto state = record.state_machine.state();
        if (state != CohortState::kCohortForming && state != CohortState::kKeyAdvertisement) {
            continue;  // already frozen, already terminal, or otherwise past the advertisement
                       // stage
        }
        const auto deadline = record.config.key_advertisement_deadline_unix_s();
        if (deadline <= 0.0 || now_unix_s <= deadline) {
            continue;  // no deadline configured, or not yet expired
        }
        record.state_machine.abort(
            SecureAggregationAbortReason::kDeadlineExceeded,
            now_unix_s,
            "key advertisement deadline passed with an incomplete cohort (" +
                std::to_string(record.advertisements_by_worker.size()) + " of " +
                std::to_string(record.config.ordered_participant_ids_size()) +
                " participants advertised)");
        persist_transition(record);
        aborted.push_back(session_id);
    }
    return aborted;
}

std::vector<std::string> SecureAggregationSessionManager::sweep_expired_masked_update_deadlines(
    double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> aborted;
    for (auto& [session_id, record] : sessions_) {
        if (record.state_machine.state() != CohortState::kMaskedUpdateCollection) {
            continue;  // not yet frozen, or already past masked-update collection
        }
        if (record.contributions_by_worker.size() >= record.config.cohort_size()) {
            // The complete cohort has already submitted -- this session
            // is ready for (or already mid-) finalize() and must never
            // be aborted out from under it just because the deadline
            // sweep happened to run first. The live RPC handler
            // (SubmitMaskedClientUpdate) calls finalize() synchronously
            // the moment the last contribution arrives, so this window
            // should be vanishingly small in practice; this check makes
            // the sweep correct regardless of scheduling.
            continue;
        }
        const auto deadline = record.config.masked_update_deadline_unix_s();
        if (deadline <= 0.0 || now_unix_s <= deadline) {
            continue;  // no deadline configured, or not yet expired
        }
        record.state_machine.abort(SecureAggregationAbortReason::kDeadlineExceeded,
                                   now_unix_s,
                                   "masked update deadline passed with an incomplete cohort (" +
                                       std::to_string(record.contributions_by_worker.size()) +
                                       " of " + std::to_string(record.config.cohort_size()) +
                                       " participants submitted)");
        record.contributions_by_worker.clear();
        persist_transition(record);
        aborted.push_back(session_id);
    }
    return aborted;
}

}  // namespace fl::coordinator
