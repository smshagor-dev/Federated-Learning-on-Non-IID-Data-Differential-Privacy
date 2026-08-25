#include "fl_coordinator/replay_protection_store.hpp"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace fl::coordinator {

namespace {

// Same FNV-1a convention as WorkerIdentityRegistry/AggregatorCheckpointStore
// -- see replay_protection_store.hpp's header comment on why nonces are
// hashed with this (not a cryptographic primitive) rather than a
// security boundary in themselves.
std::uint64_t fnv1a_hash(const std::string& data) {
    std::uint64_t hash = 1469598103934665603ULL;
    for (const unsigned char byte : data) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string hash_to_hex(std::uint64_t hash) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

std::uint64_t hex_to_hash(const std::string& hex) {
    return std::stoull(hex, nullptr, 16);
}

std::vector<std::string> split(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream(value);
    std::string item;
    while (std::getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

// worker_id/signing_key_id are not expected to contain '\x1f' (a
// non-printable ASCII unit separator) in practice; used only as an
// in-memory map key, never persisted itself (the persisted record
// stores the three components separately -- see encode_track below).
std::string track_key(const std::string& worker_id,
                      const std::string& signing_key_id,
                      MessageStream stream) {
    return worker_id + '\x1f' + signing_key_id + '\x1f' + to_string(stream);
}

}  // namespace

std::string to_string(MessageStream stream) {
    switch (stream) {
        case MessageStream::kControl:
            return "control";
        case MessageStream::kHeartbeat:
            return "heartbeat";
        case MessageStream::kTaskLifecycle:
            return "task_lifecycle";
        case MessageStream::kClientResult:
            return "client_result";
        case MessageStream::kPrivacyRecord:
            return "privacy_record";
        case MessageStream::kPersonalization:
            return "personalization";
        case MessageStream::kKeyManagement:
            return "key_management";
        case MessageStream::kSecurityEvents:
            return "security_events";
        case MessageStream::kSecureAggregation:
            return "secure_aggregation";
        case MessageStream::kSecureAggregationMaskedUpdate:
            return "secure_aggregation_masked_update";
        case MessageStream::kSecureAggregationRecovery:
            return "secure_aggregation_recovery";
        case MessageStream::kSecureAggregationRecoveryRelay:
            return "secure_aggregation_recovery_relay";
    }
    return "unknown";
}

MessageStream message_stream_from_string(const std::string& value) {
    if (value == "control")
        return MessageStream::kControl;
    if (value == "heartbeat")
        return MessageStream::kHeartbeat;
    if (value == "task_lifecycle")
        return MessageStream::kTaskLifecycle;
    if (value == "client_result")
        return MessageStream::kClientResult;
    if (value == "privacy_record")
        return MessageStream::kPrivacyRecord;
    if (value == "personalization")
        return MessageStream::kPersonalization;
    if (value == "key_management")
        return MessageStream::kKeyManagement;
    if (value == "security_events")
        return MessageStream::kSecurityEvents;
    if (value == "secure_aggregation")
        return MessageStream::kSecureAggregation;
    if (value == "secure_aggregation_masked_update")
        return MessageStream::kSecureAggregationMaskedUpdate;
    if (value == "secure_aggregation_recovery")
        return MessageStream::kSecureAggregationRecovery;
    if (value == "secure_aggregation_recovery_relay")
        return MessageStream::kSecureAggregationRecoveryRelay;
    throw ReplayProtectionStoreError("unknown message stream: " + value);
}

std::string to_string(ReplayRejectionReason reason) {
    switch (reason) {
        case ReplayRejectionReason::kNone:
            return "none";
        case ReplayRejectionReason::kDuplicateNonce:
            return "duplicate_nonce";
        case ReplayRejectionReason::kDuplicateSequence:
            return "duplicate_sequence";
        case ReplayRejectionReason::kLowerSequence:
            return "lower_sequence";
        case ReplayRejectionReason::kSequenceGapExceeded:
            return "sequence_gap_exceeded";
    }
    return "unknown";
}

ReplayProtectionStoreError::ReplayProtectionStoreError(const std::string& what)
    : std::runtime_error(what) {}

ReplayProtectionStore::ReplayProtectionStore(std::string persistence_path,
                                             std::uint64_t max_sequence_gap)
    : persistence_path_(std::move(persistence_path)), max_sequence_gap_(max_sequence_gap) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw ReplayProtectionStoreError("failed to open replay protection store file: " +
                                         persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw ReplayProtectionStoreError(
            "replay protection store file is truncated or missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    std::string checksum_line = payload.substr(marker + 1);
    const auto equals = checksum_line.find('=');
    std::string checksum_value =
        equals == std::string::npos ? "" : checksum_line.substr(equals + 1);
    while (!checksum_value.empty() &&
           (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum_value) {
        throw ReplayProtectionStoreError(
            "replay protection store checksum mismatch: file is corrupt or was truncated");
    }

    std::stringstream stream(body);
    std::string line;
    bool has_count = false;
    std::size_t expected_count = 0;
    std::size_t found_count = 0;
    while (std::getline(stream, line)) {
        if (line.empty()) {
            continue;
        }
        if (line.rfind("record_count=", 0) == 0) {
            expected_count = std::stoull(line.substr(std::string("record_count=").size()));
            has_count = true;
            continue;
        }
        if (line.rfind("record=", 0) != 0) {
            continue;
        }
        const auto parts = split(line.substr(std::string("record=").size()), '\t');
        if (parts.size() != 6 && parts.size() != 5) {
            throw ReplayProtectionStoreError("malformed replay protection store record line");
        }
        Track track;
        try {
            track.worker_id = parts[0];
            track.signing_key_id = parts[1];
            track.message_stream = message_stream_from_string(parts[2]);
            track.last_sequence_number = std::stoull(parts[3]);
            track.updated_at_unix_s = std::stod(parts[4]);
            if (parts.size() == 6 && !parts[5].empty()) {
                for (const auto& nonce_field : split(parts[5], ',')) {
                    const auto nonce_parts = split(nonce_field, ':');
                    if (nonce_parts.size() != 2) {
                        throw ReplayProtectionStoreError(
                            "malformed nonce entry in replay protection store record");
                    }
                    NonceEntry entry;
                    entry.nonce_hash = hex_to_hash(nonce_parts[0]);
                    entry.expires_at_unix_s = std::stod(nonce_parts[1]);
                    track.recent_nonce_hashes.push_back(entry);
                }
            }
        } catch (const ReplayProtectionStoreError&) {
            throw;
        } catch (const std::exception& error) {
            throw ReplayProtectionStoreError(
                std::string("replay protection store field parse failure: ") + error.what());
        }
        const auto key = track_key(track.worker_id, track.signing_key_id, track.message_stream);
        tracks_.emplace(key, std::move(track));
        ++found_count;
    }
    if (!has_count) {
        throw ReplayProtectionStoreError("replay protection store file missing record_count");
    }
    if (found_count != expected_count) {
        throw ReplayProtectionStoreError("replay protection store file truncated: expected " +
                                         std::to_string(expected_count) + " records, found " +
                                         std::to_string(found_count));
    }
}

void ReplayProtectionStore::persist() const {
    std::ostringstream body;
    body << "record_count=" << tracks_.size() << "\n";
    for (const auto& [key, track] : tracks_) {
        body << "record=" << track.worker_id << "\t" << track.signing_key_id << "\t"
             << to_string(track.message_stream) << "\t" << track.last_sequence_number << "\t"
             << std::setprecision(17) << track.updated_at_unix_s << "\t";
        for (std::size_t i = 0; i < track.recent_nonce_hashes.size(); ++i) {
            if (i > 0) {
                body << ",";
            }
            body << hash_to_hex(track.recent_nonce_hashes[i].nonce_hash) << ":"
                 << std::setprecision(17) << track.recent_nonce_hashes[i].expires_at_unix_s;
        }
        body << "\n";
    }
    const auto body_str = body.str();
    std::ostringstream out;
    out << body_str;
    out << "checksum=" << hash_to_hex(fnv1a_hash(body_str)) << "\n";

    const std::filesystem::path target(persistence_path_);
    if (target.has_parent_path()) {
        std::filesystem::create_directories(target.parent_path());
    }
    const auto temp_path = persistence_path_ + ".tmp";
    {
        std::ofstream file(temp_path, std::ios::binary | std::ios::trunc);
        if (!file) {
            throw ReplayProtectionStoreError("failed to open replay protection store temp file: " +
                                             temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw ReplayProtectionStoreError("failed to write replay protection store temp file: " +
                                             temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw ReplayProtectionStoreError(
                "failed to atomically move replay protection store into place: " +
                error_code.message());
        }
    }
}

ReplayDecision ReplayProtectionStore::validate(const ReplayCandidate& candidate) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto key =
        track_key(candidate.worker_id, candidate.signing_key_id, candidate.message_stream);
    const auto it = tracks_.find(key);

    const std::uint64_t nonce_hash = fnv1a_hash(candidate.nonce);
    if (it != tracks_.end()) {
        for (const auto& entry : it->second.recent_nonce_hashes) {
            if (entry.nonce_hash == nonce_hash && candidate.now_unix_s < entry.expires_at_unix_s) {
                return {false,
                        ReplayRejectionReason::kDuplicateNonce,
                        "nonce already used for this worker/signing-key/stream and has not yet "
                        "expired from replay tracking"};
            }
        }
    }

    const std::uint64_t last_sequence = (it != tracks_.end()) ? it->second.last_sequence_number : 0;
    if (candidate.sequence_number <= last_sequence) {
        if (candidate.sequence_number == last_sequence && last_sequence != 0) {
            return {false,
                    ReplayRejectionReason::kDuplicateSequence,
                    "sequence_number equals the last accepted sequence for this track"};
        }
        return {false,
                ReplayRejectionReason::kLowerSequence,
                "sequence_number is not greater than the last accepted sequence for this track "
                "(the documented starting value for a new track is 1)"};
    }
    const std::uint64_t gap = candidate.sequence_number - last_sequence;
    if (gap > max_sequence_gap_) {
        return {false,
                ReplayRejectionReason::kSequenceGapExceeded,
                "sequence_number is more than max_sequence_gap ahead of the last accepted "
                "sequence for this track"};
    }

    return {true, ReplayRejectionReason::kNone, "ok"};
}

void ReplayProtectionStore::commit(const ReplayCandidate& candidate) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto key =
        track_key(candidate.worker_id, candidate.signing_key_id, candidate.message_stream);

    if (tracks_.find(key) == tracks_.end() && tracks_.size() >= kMaxTracks) {
        // Bounded-tracks eviction (Work Package E's "Maximum workers"
        // requirement): make room by evicting the single
        // least-recently-updated track. This is a graceful-degradation
        // policy, not a security boundary -- it only ever discards
        // replay/sequence *history*, which at worst re-permits a
        // sequence number an evicted track had already used (a
        // narrowed, not eliminated, protection), never grants any
        // capability an attacker didn't already have from a live,
        // valid signing key.
        auto oldest = tracks_.begin();
        for (auto candidate_it = tracks_.begin(); candidate_it != tracks_.end(); ++candidate_it) {
            if (candidate_it->second.updated_at_unix_s < oldest->second.updated_at_unix_s) {
                oldest = candidate_it;
            }
        }
        tracks_.erase(oldest);
    }

    Track& track = tracks_[key];
    track.worker_id = candidate.worker_id;
    track.signing_key_id = candidate.signing_key_id;
    track.message_stream = candidate.message_stream;
    track.last_sequence_number = candidate.sequence_number;
    track.updated_at_unix_s = candidate.now_unix_s;

    NonceEntry entry;
    entry.nonce_hash = fnv1a_hash(candidate.nonce);
    entry.expires_at_unix_s = candidate.now_unix_s + candidate.nonce_retention_seconds;
    track.recent_nonce_hashes.push_back(entry);
    if (track.recent_nonce_hashes.size() > kMaxNonceEntriesPerTrack) {
        track.recent_nonce_hashes.erase(track.recent_nonce_hashes.begin());
    }

    persist();
}

void ReplayProtectionStore::purge_expired(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    bool changed = false;
    for (auto& [key, track] : tracks_) {
        const auto before = track.recent_nonce_hashes.size();
        track.recent_nonce_hashes.erase(std::remove_if(track.recent_nonce_hashes.begin(),
                                                       track.recent_nonce_hashes.end(),
                                                       [now_unix_s](const NonceEntry& entry) {
                                                           return entry.expires_at_unix_s <=
                                                                  now_unix_s;
                                                       }),
                                        track.recent_nonce_hashes.end());
        if (track.recent_nonce_hashes.size() != before) {
            changed = true;
        }
    }
    if (changed) {
        persist();
    }
}

void ReplayProtectionStore::purge_worker(const std::string& worker_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    bool changed = false;
    for (auto it = tracks_.begin(); it != tracks_.end();) {
        if (it->second.worker_id == worker_id) {
            it = tracks_.erase(it);
            changed = true;
        } else {
            ++it;
        }
    }
    if (changed) {
        persist();
    }
}

}  // namespace fl::coordinator
