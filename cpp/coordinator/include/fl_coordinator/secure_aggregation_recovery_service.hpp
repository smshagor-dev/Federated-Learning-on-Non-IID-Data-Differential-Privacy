#pragma once

#include "fl_coordinator/peer_identity.hpp"
#include "fl_coordinator/replay_protection_store.hpp"
#include "fl_coordinator/run_manager.hpp"
#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_mask.hpp"
#include "fl_coordinator/secure_aggregation_session_manager.hpp"
#include "fl_coordinator/secure_aggregation_tensor_mask.hpp"
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "fl_coordinator/signing_key_registry.hpp"
#include "fl_coordinator/worker_identity_registry.hpp"

#include "recovery/recovery.grpc.pb.h"

#include <grpcpp/grpcpp.h>
#include <openssl/bn.h>
#include <openssl/evp.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace fl::coordinator {

// v3 live threshold-recovery adapter. The service shares the coordinator's
// transport, identity, signing-key, replay and secure-session authorities; it
// does not create a parallel trust database.
class SecureAggregationRecoveryServiceImpl final
    : public fl::recovery::v1::SecureAggregationRecoveryService::Service {
  public:
    SecureAggregationRecoveryServiceImpl(RunManager& run_manager,
                                         WorkerIdentityRegistry& identity_registry,
                                         SigningKeyRegistry& signing_key_registry,
                                         ReplayProtectionStore& replay_store,
                                         SecureAggregationSessionManager& session_manager)
        : run_manager_(&run_manager),
          identity_registry_(&identity_registry),
          signing_key_registry_(&signing_key_registry),
          replay_store_(&replay_store),
          session_manager_(&session_manager) {}

    grpc::Status SubmitRecoveryShare(
        grpc::ServerContext* context,
        const fl::recovery::v1::SubmitRecoveryShareRequest* request,
        fl::recovery::v1::SubmitRecoveryShareResponse* response) override {
        const double now = now_unix_s();
        if (request == nullptr || response == nullptr || !request->has_envelope() ||
            !request->has_recovery_share()) {
            return reject(response, "recovery_request_missing", "envelope and recovery_share are required");
        }

        const auto& envelope = request->envelope();
        const auto& share = request->recovery_share();
        std::string validation_error;
        if (!validate_payload_shape(share, validation_error)) {
            return reject(response, "recovery_payload_invalid", validation_error);
        }
        if (envelope.worker_id() != share.holder_worker_id() || envelope.run_id() != share.run_id() ||
            envelope.round_id() != share.round_id() ||
            envelope.model_version() != share.model_version() || !envelope.task_id().empty() ||
            !envelope.client_id().empty()) {
            return reject(response,
                          "recovery_envelope_binding_mismatch",
                          "recovery envelope does not match holder/run/round/model bindings");
        }
        if (envelope.message_stream() !=
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_SECURE_AGGREGATION) {
            return reject(response,
                          "recovery_message_stream_invalid",
                          "recovery envelope must use the secure-aggregation wire category");
        }
        if (envelope.issued_at() != share.issued_at() || envelope.expires_at() != share.expires_at()) {
            return reject(response,
                          "recovery_timestamp_binding_mismatch",
                          "recovery payload/envelope timestamps must match exactly");
        }

        const auto identity = identity_registry_->find_by_worker_id(share.holder_worker_id());
        if (!identity.has_value() || identity->registration_status != WorkerIdentityStatus::kActive) {
            return reject(response, "recovery_worker_not_active", "recovery holder is not ACTIVE");
        }
        if (identity->expires_at_unix_s > 0.0 && now >= identity->expires_at_unix_s) {
            return reject(response, "recovery_worker_expired", "recovery holder identity is expired");
        }

        if (context != nullptr) {
            const auto peer = extract_peer_identity(*context);
            if (peer.authenticated) {
                if (!has_worker_identity(peer, share.holder_worker_id())) {
                    return reject(response,
                                  "recovery_mtls_identity_mismatch",
                                  "mTLS worker identity does not match recovery holder");
                }
                if (!identity->certificate_fingerprint.empty() &&
                    peer.certificate_fingerprint_sha256 != identity->certificate_fingerprint) {
                    return reject(response,
                                  "recovery_certificate_mismatch",
                                  "mTLS certificate fingerprint does not match registered identity");
                }
            }
        }

        const auto signing_key = signing_key_registry_->find(
            share.holder_worker_id(), envelope.signing_key_id(), now);
        if (!signing_key.has_value() ||
            (signing_key->status != SigningKeyStatus::kActive &&
             signing_key->status != SigningKeyStatus::kGracePeriod)) {
            return reject(response,
                          "recovery_signing_key_invalid",
                          "recovery signing key is unknown, expired, or revoked");
        }

        const auto payload_hash_input = recovery_payload_hash_input(share);
        const auto verification = verify_signed_envelope(
            envelope,
            /*expected_message_type=*/15,
            payload_hash_input,
            signing_key->public_key_hex,
            now,
            /*future_issued_tolerance_seconds=*/30.0);
        if (!verification.valid) {
            return reject(response,
                          verification.rejection_code.empty() ? "recovery_signature_invalid"
                                                              : verification.rejection_code,
                          verification.reason);
        }

        ReplayCandidate replay;
        replay.worker_id = share.holder_worker_id();
        replay.signing_key_id = envelope.signing_key_id();
        replay.message_stream = MessageStream::kSecureAggregationRecovery;
        replay.sequence_number = envelope.sequence_number();
        replay.nonce = envelope.nonce();
        replay.now_unix_s = now;
        replay.nonce_retention_seconds = std::max(1.0, envelope.expires_at() - now);
        const auto replay_decision = replay_store_->validate(replay);
        if (!replay_decision.accepted) {
            return reject(response,
                          "recovery_" + to_string(replay_decision.reason),
                          replay_decision.detail);
        }

        const auto view = session_manager_->recovery_view(share.session_id());
        if (!view.has_value()) {
            return reject(response,
                          "recovery_session_not_ready",
                          "session is unknown, not frozen, or has no established masked tensor shape");
        }
        if (share.run_id() != view->config.run_id() || share.round_id() != view->config.round_id() ||
            share.model_version() != view->config.model_version() ||
            share.cohort_commitment() != view->frozen_roster.cohort_commitment()) {
            return reject(response,
                          "recovery_session_binding_mismatch",
                          "share does not match the frozen secure-aggregation session");
        }

        std::set<std::string> participants;
        std::map<std::string, std::string> public_keys_hex;
        std::map<std::string, std::string> client_ids;
        for (const auto& participant : view->frozen_roster.participants()) {
            participants.insert(participant.worker_id());
            public_keys_hex[participant.worker_id()] = participant.ephemeral_public_key_x25519();
            client_ids[participant.worker_id()] = participant.client_id();
        }
        std::set<std::string> contributors(view->contributing_worker_ids.begin(),
                                           view->contributing_worker_ids.end());
        std::vector<std::string> missing;
        std::set_difference(participants.begin(),
                            participants.end(),
                            contributors.begin(),
                            contributors.end(),
                            std::back_inserter(missing));
        if (missing.size() != 1 || missing.front() != share.owner_worker_id()) {
            return reject(response,
                          "recovery_owner_not_single_dropout",
                          "initial live recovery supports exactly one missing contributor");
        }
        if (contributors.count(share.holder_worker_id()) == 0) {
            return reject(response,
                          "recovery_holder_not_survivor",
                          "recovery holder must already be a submitted survivor");
        }
        if (share.total_shares() != participants.size() - 1 ||
            share.threshold() > contributors.size()) {
            return reject(response,
                          "recovery_threshold_incompatible",
                          "share threshold/total_shares is incompatible with the frozen cohort");
        }

        RunInstance* run = nullptr;
        try {
            run = &run_manager_->get(share.run_id());
        } catch (const std::exception& error) {
            return reject(response, "recovery_run_unknown", error.what());
        }
        if (run->privacy_mode() != fl::core::PrivacyMode::kNone || run->adaptive_clipping_enabled()) {
            return reject(response,
                          "recovery_privacy_mode_unsupported",
                          "live threshold recovery currently supports non-private fixed-clipping rounds only");
        }
        if (run->algorithm() != fl::core::AggregationAlgorithm::kFedAvg) {
            return reject(response,
                          "recovery_algorithm_unsupported",
                          "live threshold recovery currently supports secure FedAvg only");
        }

        const RecoveryKey key{share.session_id(), share.owner_worker_id(), share.generation()};
        std::optional<std::string> recovered_private_key;
        std::uint32_t submitted_count = 0;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            RecoveryBucket candidate = buckets_[key];
            std::string bucket_error;
            if (!candidate.add(share, bucket_error)) {
                return reject(response, "recovery_share_conflict", bucket_error);
            }
            submitted_count = static_cast<std::uint32_t>(candidate.shares_by_holder.size());
            if (candidate.can_recover()) {
                try {
                    recovered_private_key = reconstruct_secret(candidate);
                    verify_recovered_x25519_public_key(
                        *recovered_private_key, public_keys_hex.at(share.owner_worker_id()));
                    candidate.recovered = true;
                } catch (const std::exception& error) {
                    return reject(response, "recovery_reconstruction_failed", error.what());
                }
            }
            buckets_[key] = std::move(candidate);
        }

        // Domain admission succeeded. Commit replay state only now; malformed,
        // unbound, conflicting, or unreconstructable shares never advance it.
        replay_store_->commit(replay);
        response->set_accepted(true);
        response->set_submitted_share_count(submitted_count);
        response->set_threshold(share.threshold());
        response->set_recoverable(recovered_private_key.has_value());

        if (!recovered_private_key.has_value()) {
            response->set_reason("share accepted; threshold not reached");
            return grpc::Status::OK;
        }

        try {
            const auto correction = build_correction_update(
                *view,
                share.owner_worker_id(),
                client_ids.at(share.owner_worker_id()),
                *recovered_private_key,
                public_keys_hex,
                contributors,
                now);
            const auto status = session_manager_->submit_masked_update(correction, now);
            if (status.masked_contribution_count() != status.cohort_size()) {
                response->set_reason("recovery correction accepted but cohort is still incomplete");
                return grpc::Status::OK;
            }

            const auto aggregate = session_manager_->finalize(share.session_id(), now);
            const bool advanced = run->apply_secure_aggregate_and_advance(
                share.round_id(), aggregate, now, std::nullopt);
            response->set_recovery_applied(true);
            response->set_round_advanced(advanced);
            response->set_reason(advanced ? "threshold recovery finalized the secure round"
                                          : "secure aggregate recovered but run did not advance");
            {
                std::lock_guard<std::mutex> lock(mutex_);
                auto& bucket = buckets_.at(key);
                bucket.applied = true;
                bucket.round_advanced = advanced;
            }
            return grpc::Status::OK;
        } catch (const std::exception& error) {
            response->set_reason(std::string("share threshold recovered but correction/finalization failed: ") +
                                 error.what());
            return grpc::Status::OK;
        }
    }

    grpc::Status GetRecoveryStatus(
        grpc::ServerContext*,
        const fl::recovery::v1::GetRecoveryStatusRequest* request,
        fl::recovery::v1::GetRecoveryStatusResponse* response) override {
        if (request == nullptr || response == nullptr || request->session_id().empty() ||
            request->owner_worker_id().empty()) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                                "session_id and owner_worker_id are required");
        }
        const RecoveryKey key{request->session_id(), request->owner_worker_id(), request->generation()};
        std::lock_guard<std::mutex> lock(mutex_);
        const auto it = buckets_.find(key);
        if (it == buckets_.end()) {
            response->set_found(false);
            return grpc::Status::OK;
        }
        response->set_found(true);
        response->set_session_id(key.session_id);
        response->set_owner_worker_id(key.owner_worker_id);
        response->set_generation(key.generation);
        response->set_submitted_share_count(
            static_cast<std::uint32_t>(it->second.shares_by_holder.size()));
        response->set_threshold(it->second.threshold);
        response->set_recoverable(it->second.can_recover());
        response->set_recovered(it->second.recovered);
        response->set_recovery_applied(it->second.applied);
        response->set_state(it->second.applied
                                ? (it->second.round_advanced ? "round_advanced" : "aggregate_recovered")
                                : (it->second.recovered ? "key_recovered" : "collecting_shares"));
        for (const auto& [holder, stored] : it->second.shares_by_holder) {
            auto* receipt = response->add_receipts();
            receipt->set_holder_worker_id(holder);
            receipt->set_share_index(stored.share_index());
            receipt->set_share_commitment(share_commitment(stored));
        }
        return grpc::Status::OK;
    }

  private:
    struct RecoveryKey {
        std::string session_id;
        std::string owner_worker_id;
        std::uint32_t generation = 0;

        bool operator<(const RecoveryKey& other) const {
            return std::tie(session_id, owner_worker_id, generation) <
                   std::tie(other.session_id, other.owner_worker_id, other.generation);
        }
    };

    struct RecoveryBucket {
        std::uint32_t threshold = 0;
        std::uint32_t total_shares = 0;
        std::uint32_t secret_length = 0;
        std::string secret_digest;
        std::string field_id;
        std::map<std::string, fl::worker::v1::SecureAggregationRecoveryShare> shares_by_holder;
        bool recovered = false;
        bool applied = false;
        bool round_advanced = false;

        bool add(const fl::worker::v1::SecureAggregationRecoveryShare& share,
                 std::string& error) {
            if (shares_by_holder.empty()) {
                threshold = share.threshold();
                total_shares = share.total_shares();
                secret_length = share.secret_length();
                secret_digest = share.secret_digest();
                field_id = share.field_id();
            } else if (threshold != share.threshold() || total_shares != share.total_shares() ||
                       secret_length != share.secret_length() || secret_digest != share.secret_digest() ||
                       field_id != share.field_id()) {
                error = "recovery share metadata conflicts with previously accepted shares";
                return false;
            }
            for (const auto& [holder, existing] : shares_by_holder) {
                if (holder != share.holder_worker_id() && existing.share_index() == share.share_index()) {
                    error = "duplicate recovery share index";
                    return false;
                }
            }
            const auto it = shares_by_holder.find(share.holder_worker_id());
            if (it != shares_by_holder.end()) {
                if (it->second.SerializeAsString() == share.SerializeAsString()) {
                    return true;
                }
                error = "holder submitted a conflicting recovery share";
                return false;
            }
            shares_by_holder.emplace(share.holder_worker_id(), share);
            return true;
        }

        [[nodiscard]] bool can_recover() const {
            return threshold >= 2 && shares_by_holder.size() >= threshold;
        }
    };

    using BnPtr = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
    using BnCtxPtr = std::unique_ptr<BN_CTX, decltype(&BN_CTX_free)>;
    using EvpKeyPtr = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;

    static double now_unix_s() {
        using namespace std::chrono;
        return duration_cast<duration<double>>(system_clock::now().time_since_epoch()).count();
    }

    static grpc::Status reject(fl::recovery::v1::SubmitRecoveryShareResponse* response,
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
            return std::isalnum(ch) || ch == '.' || ch == '_' || ch == ':' || ch == '/' ||
                   ch == '@' || ch == '+' || ch == '-';
        });
    }

    static bool lowercase_hex(const std::string& value, std::size_t exact_length = 0) {
        if (value.empty() || (exact_length != 0 && value.size() != exact_length)) {
            return false;
        }
        return std::all_of(value.begin(), value.end(), [](unsigned char ch) {
            return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
        });
    }

    static bool validate_payload_shape(const fl::worker::v1::SecureAggregationRecoveryShare& share,
                                       std::string& error) {
        if (share.schema_version() != 1) {
            error = "unsupported recovery share schema_version";
            return false;
        }
        for (const auto* value : {&share.session_id(),
                                  &share.run_id(),
                                  &share.model_version(),
                                  &share.owner_worker_id(),
                                  &share.holder_worker_id()}) {
            if (!safe_binding(*value)) {
                error = "recovery binding contains an invalid identifier";
                return false;
            }
        }
        if (share.owner_worker_id() == share.holder_worker_id()) {
            error = "recovery owner and holder must differ";
            return false;
        }
        if (!lowercase_hex(share.cohort_commitment(), 64) ||
            !lowercase_hex(share.secret_digest(), 64)) {
            error = "recovery commitment/digest must be lowercase SHA-256 hex";
            return false;
        }
        if (share.field_id() != "mersenne-521-v1" || share.secret_length() != 32) {
            error = "live recovery requires a 32-byte X25519 secret in mersenne-521-v1";
            return false;
        }
        if (share.threshold() < 2 || share.threshold() > share.total_shares() ||
            share.share_index() < 1 || share.share_index() > share.total_shares()) {
            error = "invalid threshold/total_shares/share_index";
            return false;
        }
        if (share.share_value_hex().size() > 132 ||
            !lowercase_hex(share.share_value_hex()) ||
            (share.share_value_hex().size() > 1 && share.share_value_hex().front() == '0')) {
            error = "share_value_hex is not canonical field hex";
            return false;
        }
        if (!std::isfinite(share.issued_at()) || !std::isfinite(share.expires_at()) ||
            share.expires_at() <= share.issued_at()) {
            error = "invalid recovery share timestamps";
            return false;
        }
        return true;
    }

    static std::string format_double(double value) {
        std::ostringstream out;
        out << std::setprecision(17) << std::defaultfloat << value;
        return out.str();
    }

    static std::string recovery_payload_hash_input(
        const fl::worker::v1::SecureAggregationRecoveryShare& share) {
        std::ostringstream out;
        out << "{"
            << "\"cohort_commitment\":\"" << share.cohort_commitment() << "\","
            << "\"expires_at\":" << format_double(share.expires_at()) << ","
            << "\"field_id\":\"" << share.field_id() << "\","
            << "\"generation\":" << share.generation() << ","
            << "\"holder_worker_id\":\"" << share.holder_worker_id() << "\","
            << "\"issued_at\":" << format_double(share.issued_at()) << ","
            << "\"model_version\":\"" << share.model_version() << "\","
            << "\"owner_worker_id\":\"" << share.owner_worker_id() << "\","
            << "\"round_id\":" << share.round_id() << ","
            << "\"run_id\":\"" << share.run_id() << "\","
            << "\"schema_version\":" << share.schema_version() << ","
            << "\"secret_digest\":\"" << share.secret_digest() << "\","
            << "\"secret_length\":" << share.secret_length() << ","
            << "\"session_id\":\"" << share.session_id() << "\","
            << "\"share_index\":" << share.share_index() << ","
            << "\"share_value_hex\":\"" << share.share_value_hex() << "\","
            << "\"threshold\":" << share.threshold() << ","
            << "\"total_shares\":" << share.total_shares() << "}";
        return out.str();
    }

    static BnPtr recovery_prime() {
        BnPtr prime(BN_new(), &BN_free);
        if (!prime || BN_one(prime.get()) != 1 || BN_lshift(prime.get(), prime.get(), 521) != 1 ||
            BN_sub_word(prime.get(), 1) != 1) {
            throw std::runtime_error("failed to construct Mersenne-521 recovery field");
        }
        return prime;
    }

    static std::string reconstruct_secret(const RecoveryBucket& bucket) {
        if (!bucket.can_recover()) {
            throw std::runtime_error("insufficient recovery shares");
        }
        BnCtxPtr ctx(BN_CTX_new(), &BN_CTX_free);
        auto prime = recovery_prime();
        BnPtr secret(BN_new(), &BN_free);
        if (!ctx || !secret || BN_zero(secret.get()) != 1) {
            throw std::runtime_error("failed to initialize recovery interpolation");
        }

        std::vector<const fl::worker::v1::SecureAggregationRecoveryShare*> selected;
        selected.reserve(bucket.shares_by_holder.size());
        for (const auto& [holder, share] : bucket.shares_by_holder) {
            (void)holder;
            selected.push_back(&share);
        }
        std::sort(selected.begin(), selected.end(), [](const auto* left, const auto* right) {
            return left->share_index() < right->share_index();
        });
        selected.resize(bucket.threshold);

        for (std::size_t i = 0; i < selected.size(); ++i) {
            BnPtr y(nullptr, &BN_free);
            BIGNUM* parsed = nullptr;
            if (BN_hex2bn(&parsed, selected[i]->share_value_hex().c_str()) == 0 || parsed == nullptr) {
                throw std::runtime_error("failed to parse recovery share field element");
            }
            y.reset(parsed);
            if (BN_cmp(y.get(), prime.get()) >= 0) {
                throw std::runtime_error("recovery share value is outside the configured field");
            }

            BnPtr numerator(BN_new(), &BN_free);
            BnPtr denominator(BN_new(), &BN_free);
            BnPtr xi(BN_new(), &BN_free);
            BnPtr term(BN_new(), &BN_free);
            if (!numerator || !denominator || !xi || !term || BN_one(numerator.get()) != 1 ||
                BN_one(denominator.get()) != 1 ||
                BN_set_word(xi.get(), selected[i]->share_index()) != 1) {
                throw std::runtime_error("failed to initialize recovery interpolation term");
            }
            for (std::size_t j = 0; j < selected.size(); ++j) {
                if (i == j) {
                    continue;
                }
                BnPtr xj(BN_new(), &BN_free);
                BnPtr neg_xj(BN_new(), &BN_free);
                BnPtr diff(BN_new(), &BN_free);
                if (!xj || !neg_xj || !diff ||
                    BN_set_word(xj.get(), selected[j]->share_index()) != 1 ||
                    BN_zero(neg_xj.get()) != 1 ||
                    BN_mod_sub(neg_xj.get(), neg_xj.get(), xj.get(), prime.get(), ctx.get()) != 1 ||
                    BN_mod_mul(numerator.get(), numerator.get(), neg_xj.get(), prime.get(), ctx.get()) != 1 ||
                    BN_mod_sub(diff.get(), xi.get(), xj.get(), prime.get(), ctx.get()) != 1 ||
                    BN_mod_mul(denominator.get(), denominator.get(), diff.get(), prime.get(), ctx.get()) != 1) {
                    throw std::runtime_error("failed to compute recovery Lagrange coefficient");
                }
            }
            BIGNUM* inverse_raw = BN_mod_inverse(nullptr, denominator.get(), prime.get(), ctx.get());
            BnPtr inverse(inverse_raw, &BN_free);
            if (!inverse ||
                BN_mod_mul(term.get(), numerator.get(), inverse.get(), prime.get(), ctx.get()) != 1 ||
                BN_mod_mul(term.get(), term.get(), y.get(), prime.get(), ctx.get()) != 1 ||
                BN_mod_add(secret.get(), secret.get(), term.get(), prime.get(), ctx.get()) != 1) {
                throw std::runtime_error("failed to interpolate recovery secret");
            }
        }

        std::string raw(bucket.secret_length, '\0');
        if (BN_bn2binpad(secret.get(),
                         reinterpret_cast<unsigned char*>(raw.data()),
                         static_cast<int>(raw.size())) != static_cast<int>(raw.size())) {
            throw std::runtime_error("recovered secret exceeds declared secret length");
        }
        if (recovery_secret_digest(raw, selected.front()->session_id(), selected.front()->owner_worker_id(),
                                   selected.front()->generation()) != bucket.secret_digest) {
            throw std::runtime_error("reconstructed recovery secret failed context digest validation");
        }
        return raw;
    }

    static std::string recovery_secret_digest(const std::string& secret,
                                              const std::string& session_id,
                                              const std::string& owner_worker_id,
                                              std::uint32_t generation) {
        std::string payload = "fl-platform-secagg-threshold-v1";
        payload.push_back('\0');
        payload += session_id;
        payload.push_back('\0');
        payload += owner_worker_id;
        payload.push_back('\0');
        payload += std::to_string(generation);
        payload.push_back('\0');
        const std::uint16_t size = static_cast<std::uint16_t>(secret.size());
        payload.push_back(static_cast<char>((size >> 8) & 0xff));
        payload.push_back(static_cast<char>(size & 0xff));
        payload += secret;
        return sha256_hex(payload);
    }

    static void verify_recovered_x25519_public_key(const std::string& private_key_raw,
                                                    const std::string& expected_public_key_hex) {
        if (private_key_raw.size() != kX25519KeyLength) {
            throw std::runtime_error("recovered X25519 private key is not 32 bytes");
        }
        const auto expected = hex_decode(expected_public_key_hex);
        if (expected.size() != kX25519KeyLength) {
            throw std::runtime_error("frozen roster X25519 public key is invalid");
        }
        EvpKeyPtr key(EVP_PKEY_new_raw_private_key(EVP_PKEY_X25519,
                                                   nullptr,
                                                   reinterpret_cast<const unsigned char*>(
                                                       private_key_raw.data()),
                                                   private_key_raw.size()),
                      &EVP_PKEY_free);
        if (!key) {
            throw std::runtime_error("failed to reconstruct X25519 private key object");
        }
        std::string derived(kX25519KeyLength, '\0');
        std::size_t length = derived.size();
        if (EVP_PKEY_get_raw_public_key(
                key.get(), reinterpret_cast<unsigned char*>(derived.data()), &length) != 1 ||
            length != derived.size() || derived != expected) {
            throw std::runtime_error(
                "reconstructed dropout private key does not match frozen-roster public key");
        }
    }

    static std::string canonical_mask_context(const SecureAggregationRecoveryView& view,
                                              const std::string& self_id,
                                              const std::string& peer_id,
                                              const std::string& tensor_name) {
        const auto ordered = std::minmax(self_id, peer_id);
        std::ostringstream out;
        out << "provider=" << static_cast<int>(view.config.provider()) << '\x1e'
            << "protocol_version=" << view.config.protocol_version() << '\x1e'
            << "session_id=" << view.config.session_id() << '\x1e'
            << "run_id=" << view.config.run_id() << '\x1e'
            << "round_id=" << view.config.round_id() << '\x1e'
            << "model_version=" << view.config.model_version() << '\x1e'
            << "cohort_commitment=" << view.frozen_roster.cohort_commitment() << '\x1e'
            << "participant_low=" << ordered.first << '\x1e'
            << "participant_high=" << ordered.second << '\x1e'
            << "tensor_name=" << tensor_name << '\x1e'
            << "chunk_index=0";
        return out.str();
    }

    static std::string little_endian_bytes(const std::vector<std::uint64_t>& values) {
        std::string out(values.size() * 8, '\0');
        for (std::size_t i = 0; i < values.size(); ++i) {
            auto value = values[i];
            for (std::size_t byte = 0; byte < 8; ++byte) {
                out[i * 8 + byte] = static_cast<char>(value & 0xff);
                value >>= 8;
            }
        }
        return out;
    }

    static std::string masked_values_checksum(const std::vector<std::uint64_t>& values) {
        return sha256_hex(little_endian_bytes(values));
    }

    static std::string share_commitment(
        const fl::worker::v1::SecureAggregationRecoveryShare& share) {
        std::string payload = "fl-platform-secagg-share-receipt-v1";
        payload.push_back('\0');
        for (const auto& value : {share.session_id(),
                                  share.owner_worker_id(),
                                  share.holder_worker_id(),
                                  std::to_string(share.generation()),
                                  std::to_string(share.threshold()),
                                  std::to_string(share.total_shares()),
                                  std::to_string(share.share_index()),
                                  share.secret_digest(),
                                  std::to_string(share.secret_length()),
                                  share.field_id()}) {
            payload += value;
            payload.push_back('\0');
        }
        BIGNUM* parsed = nullptr;
        if (BN_hex2bn(&parsed, share.share_value_hex().c_str()) == 0 || parsed == nullptr) {
            throw std::runtime_error("failed to parse recovery share for commitment");
        }
        BnPtr value(parsed, &BN_free);
        std::string field_bytes(66, '\0');
        if (BN_bn2binpad(value.get(),
                         reinterpret_cast<unsigned char*>(field_bytes.data()),
                         static_cast<int>(field_bytes.size())) != static_cast<int>(field_bytes.size())) {
            throw std::runtime_error("recovery share does not fit commitment field width");
        }
        payload += field_bytes;
        return sha256_hex(payload);
    }

    static fl::worker::v1::MaskedClientUpdate build_correction_update(
        const SecureAggregationRecoveryView& view,
        const std::string& dropout_worker_id,
        const std::string& dropout_client_id,
        const std::string& dropout_private_key_raw,
        const std::map<std::string, std::string>& public_keys_hex,
        const std::set<std::string>& survivor_ids,
        double now) {
        std::map<std::string, std::vector<PeerMaskStream>> streams_by_tensor;
        for (const auto& [name, count] : view.tensor_element_counts) {
            (void)count;
            streams_by_tensor[name] = {};
        }
        std::vector<SignedMask> weight_masks;

        for (const auto& survivor_id : survivor_ids) {
            const auto public_it = public_keys_hex.find(survivor_id);
            if (public_it == public_keys_hex.end()) {
                throw std::runtime_error("survivor public key missing from frozen roster");
            }
            const auto shared = derive_x25519_shared_secret(
                dropout_private_key_raw, hex_decode(public_it->second));
            const auto sign = resolve_pairwise_mask_sign(dropout_worker_id, survivor_id);
            for (const auto& [tensor_name, count] : view.tensor_element_counts) {
                PeerMaskStream stream;
                stream.peer_participant_id = survivor_id;
                stream.sign = sign;
                stream.mask_values = derive_tensor_mask_stream(
                    shared,
                    kHkdfPurposeTensorMaskStream,
                    canonical_mask_context(view, dropout_worker_id, survivor_id, tensor_name),
                    count);
                streams_by_tensor[tensor_name].push_back(std::move(stream));
            }
            weight_masks.push_back(
                SignedMask{derive_weight_mask(shared,
                                              kHkdfPurposeWeightMaskStream,
                                              canonical_mask_context(
                                                  view, dropout_worker_id, survivor_id, "")),
                           sign});
        }

        fl::worker::v1::MaskedClientUpdate correction;
        correction.set_schema_version(1);
        correction.set_provider(view.config.provider());
        correction.set_protocol_version(view.config.protocol_version());
        correction.set_session_id(view.config.session_id());
        correction.set_run_id(view.config.run_id());
        correction.set_round_id(view.config.round_id());
        correction.set_worker_id(dropout_worker_id);
        correction.set_client_id(dropout_client_id);
        correction.set_model_version(view.config.model_version());
        correction.set_cohort_commitment(view.frozen_roster.cohort_commitment());
        correction.set_tensor_manifest_hash(view.frozen_roster.tensor_manifest_hash());
        correction.set_fixed_point_profile_hash(view.frozen_roster.fixed_point_profile_hash());
        correction.set_frozen_roster_payload_hash(view.frozen_roster.payload_hash());
        correction.set_cryptographic_profile_hash(view.frozen_roster.cryptographic_profile_hash());
        correction.set_issued_at(now);
        correction.set_expires_at(view.config.session_expiry_unix_s());

        std::uint64_t total_elements = 0;
        for (const auto& [tensor_name, count] : view.tensor_element_counts) {
            const auto values = mask_tensor(std::vector<std::int64_t>(count, 0),
                                            streams_by_tensor.at(tensor_name));
            auto* tensor = correction.add_masked_tensors();
            tensor->set_tensor_name(tensor_name);
            for (const auto value : values) {
                tensor->add_masked_values(value);
            }
            tensor->set_checksum(masked_values_checksum(values));
            total_elements += count;
        }

        correction.set_masked_weight(mask_encoded_value(0, weight_masks));
        correction.set_masked_weight_checksum(
            masked_values_checksum({correction.masked_weight()}));
        auto* stats = correction.mutable_encoding_statistics();
        stats->set_total_elements(total_elements);
        stats->set_max_quantization_error(0.0);
        stats->set_mean_quantization_error(0.0);
        return correction;
    }

    RunManager* run_manager_;
    WorkerIdentityRegistry* identity_registry_;
    SigningKeyRegistry* signing_key_registry_;
    ReplayProtectionStore* replay_store_;
    SecureAggregationSessionManager* session_manager_;
    std::mutex mutex_;
    std::map<RecoveryKey, RecoveryBucket> buckets_;
};

}  // namespace fl::coordinator
