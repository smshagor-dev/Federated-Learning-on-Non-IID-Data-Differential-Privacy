#pragma once

// Ciphertext-only pre-dropout recovery share mailbox.
//
// Owners encrypt each Shamir share end-to-end to one frozen-cohort holder using
// the owner/holder ephemeral X25519 pair. This coordinator service verifies the
// owner's mTLS identity, Ed25519 envelope, frozen-roster bindings, replay state,
// and ciphertext integrity metadata, then retains only the encrypted package in
// bounded volatile memory. It has no decryption key and no raw-share field.

#include "fl_coordinator/peer_identity.hpp"
#include "fl_coordinator/replay_protection_store.hpp"
#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_session_manager.hpp"
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "fl_coordinator/signing_key_registry.hpp"
#include "fl_coordinator/worker_identity_registry.hpp"

#include "recovery/recovery.grpc.pb.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

namespace fl::coordinator {

class SecureAggregationShareRelayServiceImpl final
    : public fl::recovery::v1::SecureAggregationShareRelayService::Service {
  public:
    static constexpr std::size_t kMaxQueuedRelays = 10000;
    static constexpr std::size_t kMaxRelaysPerHolderSession = 256;
    static constexpr std::uint32_t kDefaultFetchLimit = 64;

    SecureAggregationShareRelayServiceImpl(WorkerIdentityRegistry& identity_registry,
                                           SigningKeyRegistry& signing_key_registry,
                                           ReplayProtectionStore& replay_store,
                                           SecureAggregationSessionManager& session_manager)
        : identity_registry_(&identity_registry),
          signing_key_registry_(&signing_key_registry),
          replay_store_(&replay_store),
          session_manager_(&session_manager) {}

    grpc::Status PublishRecoveryShareRelay(
        grpc::ServerContext* context,
        const fl::recovery::v1::PublishRecoveryShareRelayRequest* request,
        fl::recovery::v1::PublishRecoveryShareRelayResponse* response) override {
        const double now = now_unix_s();
        if (context == nullptr || request == nullptr || response == nullptr ||
            !request->has_envelope() || !request->has_encrypted_share()) {
            return reject(response,
                          "relay_request_missing",
                          "mTLS context, envelope, and encrypted_share are required");
        }
        const auto& envelope = request->envelope();
        const auto& relay = request->encrypted_share();

        std::string error;
        if (!validate_relay_shape(relay, error)) {
            return reject(response, "relay_payload_invalid", error);
        }
        if (envelope.worker_id() != relay.owner_worker_id() ||
            envelope.run_id() != relay.run_id() || envelope.round_id() != relay.round_id() ||
            envelope.model_version() != relay.model_version() || !envelope.task_id().empty() ||
            !envelope.client_id().empty() || envelope.issued_at() != relay.issued_at() ||
            envelope.expires_at() != relay.expires_at()) {
            return reject(response,
                          "relay_envelope_binding_mismatch",
                          "relay envelope does not match owner/run/round/model/timestamp bindings");
        }
        if (envelope.message_stream() !=
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_SECURE_AGGREGATION) {
            return reject(response,
                          "relay_message_stream_invalid",
                          "relay envelope must use secure-aggregation message stream");
        }

        const auto peer = extract_peer_identity(*context);
        if (!peer.authenticated || !has_worker_identity(peer, relay.owner_worker_id())) {
            return reject(response,
                          "relay_mtls_identity_mismatch",
                          "verified mTLS worker identity must match relay owner");
        }
        const auto identity = identity_registry_->find_by_worker_id(relay.owner_worker_id());
        if (!identity.has_value() || identity->registration_status != WorkerIdentityStatus::kActive) {
            return reject(response, "relay_owner_not_active", "relay owner is not ACTIVE");
        }
        if (identity->expires_at_unix_s > 0.0 && now >= identity->expires_at_unix_s) {
            return reject(response, "relay_owner_expired", "relay owner identity is expired");
        }
        if (!identity->certificate_fingerprint.empty() &&
            peer.certificate_fingerprint_sha256 != identity->certificate_fingerprint) {
            return reject(response,
                          "relay_certificate_mismatch",
                          "mTLS certificate fingerprint does not match registered owner");
        }

        const auto signing_key =
            signing_key_registry_->find(relay.owner_worker_id(), envelope.signing_key_id(), now);
        if (!signing_key.has_value() ||
            (signing_key->status != SigningKeyStatus::kActive &&
             signing_key->status != SigningKeyStatus::kGracePeriod)) {
            return reject(response,
                          "relay_signing_key_invalid",
                          "relay signing key is unknown, expired, or revoked");
        }
        const auto verification = verify_signed_envelope(
            envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::
                                 MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_RELAY),
            relay_payload_hash_input(relay),
            signing_key->public_key_hex,
            now,
            30.0);
        if (!verification.valid) {
            return reject(response,
                          verification.rejection_code.empty() ? "relay_signature_invalid"
                                                              : verification.rejection_code,
                          verification.reason);
        }

        ReplayCandidate replay;
        replay.worker_id = relay.owner_worker_id();
        replay.signing_key_id = envelope.signing_key_id();
        replay.message_stream = MessageStream::kSecureAggregationRecoveryRelay;
        replay.sequence_number = envelope.sequence_number();
        replay.nonce = envelope.nonce();
        replay.now_unix_s = now;
        replay.nonce_retention_seconds = std::max(1.0, envelope.expires_at() - now);
        const auto replay_decision = replay_store_->validate(replay);
        if (!replay_decision.accepted) {
            return reject(response,
                          "relay_" + to_string(replay_decision.reason),
                          replay_decision.detail);
        }

        const auto roster = session_manager_->get_frozen_roster(relay.session_id());
        if (!roster.has_value()) {
            return reject(response,
                          "relay_session_not_frozen",
                          "relay session is unknown or has no frozen roster");
        }
        if (relay.run_id() != roster->run_id() || relay.round_id() != roster->round_id() ||
            relay.model_version() != roster->model_version() ||
            relay.cohort_commitment() != roster->cohort_commitment()) {
            return reject(response,
                          "relay_session_binding_mismatch",
                          "relay metadata does not match the signed frozen roster");
        }
        std::set<std::string> participants;
        for (const auto& participant : roster->participants()) {
            participants.insert(participant.worker_id());
        }
        if (participants.count(relay.owner_worker_id()) == 0 ||
            participants.count(relay.holder_worker_id()) == 0) {
            return reject(response,
                          "relay_participant_mismatch",
                          "relay owner and holder must both belong to the frozen cohort");
        }
        if (relay.total_shares() != participants.size() - 1 ||
            relay.threshold() > relay.total_shares()) {
            return reject(response,
                          "relay_threshold_incompatible",
                          "relay threshold/total_shares does not match the frozen cohort");
        }

        {
            std::lock_guard<std::mutex> lock(mutex_);
            purge_expired_locked(now);
            const RelayKey key{relay.session_id(),
                               relay.owner_worker_id(),
                               relay.holder_worker_id(),
                               relay.generation()};
            const auto existing = relays_.find(key);
            if (existing != relays_.end()) {
                if (existing->second.SerializeAsString() != relay.SerializeAsString()) {
                    return reject(response,
                                  "relay_conflict",
                                  "owner published conflicting ciphertext for the same holder/generation");
                }
                // Idempotent domain publication under a fresh valid envelope.
                replay_store_->commit(replay);
                response->set_accepted(true);
                response->set_reason("relay already present");
                return grpc::Status::OK;
            }
            for (const auto& [other_key, other] : relays_) {
                if (other_key.session_id == relay.session_id() &&
                    other_key.owner_worker_id == relay.owner_worker_id() &&
                    other_key.generation == relay.generation() &&
                    other.share_index() == relay.share_index()) {
                    return reject(response,
                                  "relay_duplicate_share_index",
                                  "owner reused one recovery share index for multiple holders");
                }
            }
            if (relays_.size() >= kMaxQueuedRelays) {
                return reject(response, "relay_queue_full", "global relay mailbox is full");
            }
            std::size_t holder_session_count = 0;
            for (const auto& [other_key, other] : relays_) {
                (void)other;
                if (other_key.session_id == relay.session_id() &&
                    other_key.holder_worker_id == relay.holder_worker_id()) {
                    ++holder_session_count;
                }
            }
            if (holder_session_count >= kMaxRelaysPerHolderSession) {
                return reject(response,
                              "relay_holder_queue_full",
                              "holder/session relay mailbox is full");
            }
            relays_.emplace(key, relay);
        }

        replay_store_->commit(replay);
        response->set_accepted(true);
        response->set_reason("encrypted recovery share queued for holder");
        return grpc::Status::OK;
    }

    grpc::Status FetchRecoveryShareRelays(
        grpc::ServerContext* context,
        const fl::recovery::v1::FetchRecoveryShareRelaysRequest* request,
        fl::recovery::v1::FetchRecoveryShareRelaysResponse* response) override {
        if (context == nullptr || request == nullptr || response == nullptr ||
            request->holder_worker_id().empty() || request->session_id().empty()) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                                "mTLS context, holder_worker_id, and session_id are required");
        }
        const auto peer = extract_peer_identity(*context);
        if (!peer.authenticated || !has_worker_identity(peer, request->holder_worker_id())) {
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "verified mTLS worker identity must match relay holder");
        }
        const double now = now_unix_s();
        const auto identity = identity_registry_->find_by_worker_id(request->holder_worker_id());
        if (!identity.has_value() || identity->registration_status != WorkerIdentityStatus::kActive ||
            (identity->expires_at_unix_s > 0.0 && now >= identity->expires_at_unix_s) ||
            (!identity->certificate_fingerprint.empty() &&
             peer.certificate_fingerprint_sha256 != identity->certificate_fingerprint)) {
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "relay holder identity is not active/current");
        }
        const auto roster = session_manager_->get_frozen_roster(request->session_id());
        if (!roster.has_value()) {
            return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                                "relay session is unknown or not frozen");
        }
        bool holder_in_roster = false;
        for (const auto& participant : roster->participants()) {
            holder_in_roster = holder_in_roster || participant.worker_id() == request->holder_worker_id();
        }
        if (!holder_in_roster) {
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "relay holder is not a frozen-cohort participant");
        }

        const std::uint32_t limit =
            request->max_items() == 0 ? kDefaultFetchLimit : request->max_items();
        if (limit > kMaxRelaysPerHolderSession) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                                "max_items exceeds the relay mailbox bound");
        }
        std::lock_guard<std::mutex> lock(mutex_);
        purge_expired_locked(now);
        std::uint32_t emitted = 0;
        for (const auto& [key, relay] : relays_) {
            if (key.session_id == request->session_id() &&
                key.holder_worker_id == request->holder_worker_id()) {
                *response->add_encrypted_shares() = relay;
                if (++emitted >= limit) {
                    break;
                }
            }
        }
        return grpc::Status::OK;
    }

  private:
    struct RelayKey {
        std::string session_id;
        std::string owner_worker_id;
        std::string holder_worker_id;
        std::uint32_t generation = 0;

        bool operator<(const RelayKey& other) const {
            return std::tie(session_id, owner_worker_id, holder_worker_id, generation) <
                   std::tie(other.session_id,
                            other.owner_worker_id,
                            other.holder_worker_id,
                            other.generation);
        }
    };

    static double now_unix_s() {
        using namespace std::chrono;
        return duration_cast<duration<double>>(system_clock::now().time_since_epoch()).count();
    }

    static grpc::Status reject(fl::recovery::v1::PublishRecoveryShareRelayResponse* response,
                               const std::string& code,
                               const std::string& reason) {
        if (response != nullptr) {
            response->set_accepted(false);
            response->set_rejection_code(code);
            response->set_reason(reason);
        }
        return grpc::Status::OK;
    }

    static bool safe_binding(const std::string& value) {
        if (value.empty() || value.size() > 256) {
            return false;
        }
        return std::all_of(value.begin(), value.end(), [](unsigned char ch) {
            return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                   (ch >= '0' && ch <= '9') || ch == '.' || ch == '_' || ch == ':' || ch == '/' ||
                   ch == '@' || ch == '+' || ch == '-';
        });
    }

    static bool lowercase_hex(const std::string& value, std::size_t exact_length) {
        return value.size() == exact_length &&
               std::all_of(value.begin(), value.end(), [](unsigned char ch) {
                   return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
               });
    }

    static bool validate_relay_shape(const fl::recovery::v1::EncryptedRecoveryShareRelay& relay,
                                     std::string& error) {
        if (relay.schema_version() != 1) {
            error = "unsupported relay schema_version";
            return false;
        }
        for (const std::string* value : {&relay.session_id(),
                                         &relay.run_id(),
                                         &relay.model_version(),
                                         &relay.owner_worker_id(),
                                         &relay.holder_worker_id()}) {
            if (!safe_binding(*value)) {
                error = "relay contains an invalid identifier binding";
                return false;
            }
        }
        if (relay.owner_worker_id() == relay.holder_worker_id()) {
            error = "relay owner and holder must differ";
            return false;
        }
        if (!lowercase_hex(relay.cohort_commitment(), 64) ||
            !lowercase_hex(relay.secret_digest(), 64) ||
            !lowercase_hex(relay.ciphertext_hash(), 64) ||
            !lowercase_hex(relay.nonce_hex(), 24) ||
            !lowercase_hex(relay.ciphertext_hex(), 164)) {
            error = "relay cryptographic fields are not canonical lowercase hex";
            return false;
        }
        if (relay.field_id() != "mersenne-521-v1" || relay.secret_length() != 32 ||
            relay.threshold() < 2 || relay.threshold() > relay.total_shares() ||
            relay.share_index() < 1 || relay.share_index() > relay.total_shares()) {
            error = "relay threshold/share/field metadata is invalid";
            return false;
        }
        if (!std::isfinite(relay.issued_at()) || !std::isfinite(relay.expires_at()) ||
            relay.expires_at() <= relay.issued_at()) {
            error = "relay timestamps are invalid";
            return false;
        }
        try {
            if (sha256_hex(hex_decode(relay.ciphertext_hex())) != relay.ciphertext_hash()) {
                error = "relay ciphertext_hash does not match ciphertext";
                return false;
            }
        } catch (const std::exception&) {
            error = "relay ciphertext could not be decoded";
            return false;
        }
        return true;
    }

    static std::string format_double(double value) {
        std::ostringstream out;
        out << std::setprecision(17) << std::defaultfloat << value;
        return out.str();
    }

    static std::string relay_payload_hash_input(
        const fl::recovery::v1::EncryptedRecoveryShareRelay& relay) {
        std::ostringstream out;
        out << "{"
            << "\"cohort_commitment\":\"" << relay.cohort_commitment() << "\","
            << "\"expires_at\":" << format_double(relay.expires_at()) << ","
            << "\"field_id\":\"" << relay.field_id() << "\","
            << "\"generation\":" << relay.generation() << ","
            << "\"holder_worker_id\":\"" << relay.holder_worker_id() << "\","
            << "\"issued_at\":" << format_double(relay.issued_at()) << ","
            << "\"model_version\":\"" << relay.model_version() << "\","
            << "\"owner_worker_id\":\"" << relay.owner_worker_id() << "\","
            << "\"round_id\":" << relay.round_id() << ","
            << "\"run_id\":\"" << relay.run_id() << "\","
            << "\"schema_version\":" << relay.schema_version() << ","
            << "\"secret_digest\":\"" << relay.secret_digest() << "\","
            << "\"secret_length\":" << relay.secret_length() << ","
            << "\"session_id\":\"" << relay.session_id() << "\","
            << "\"share_index\":" << relay.share_index() << ","
            << "\"threshold\":" << relay.threshold() << ","
            << "\"total_shares\":" << relay.total_shares() << ","
            << "\"ciphertext_hash\":\"" << relay.ciphertext_hash() << "\","
            << "\"ciphertext_hex\":\"" << relay.ciphertext_hex() << "\","
            << "\"nonce_hex\":\"" << relay.nonce_hex() << "\"}";
        return out.str();
    }

    void purge_expired_locked(double now) {
        for (auto it = relays_.begin(); it != relays_.end();) {
            if (now >= it->second.expires_at()) {
                it = relays_.erase(it);
            } else {
                ++it;
            }
        }
    }

    WorkerIdentityRegistry* identity_registry_;
    SigningKeyRegistry* signing_key_registry_;
    ReplayProtectionStore* replay_store_;
    SecureAggregationSessionManager* session_manager_;
    std::mutex mutex_;
    std::map<RelayKey, fl::recovery::v1::EncryptedRecoveryShareRelay> relays_;
};

}  // namespace fl::coordinator
