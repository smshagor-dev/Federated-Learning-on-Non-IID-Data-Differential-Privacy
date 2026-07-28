#include "fl_coordinator/secure_aggregation_tensor_mask.hpp"

#include "fl_coordinator/secure_aggregation_crypto.hpp"

#include <cstring>
#include <stdexcept>

namespace fl::coordinator {

namespace {

// Interprets `stream` (raw ChaCha20 keystream bytes) as a sequence of
// little-endian std::uint64_t values, 8 bytes each -- matching
// Python's struct.unpack(f"<{n}Q", stream) exactly, so both languages
// derive identical mask values from an identical keystream.
std::vector<std::uint64_t> bytes_to_le_uint64_values(const std::string& stream,
                                                     std::size_t element_count) {
    std::vector<std::uint64_t> values(element_count, 0);
    for (std::size_t i = 0; i < element_count; ++i) {
        std::uint64_t value = 0;
        // Byte-by-byte little-endian assembly -- portable regardless of
        // this build machine's native endianness (never assumes the
        // host is little-endian via a raw memcpy-and-reinterpret).
        for (int byte_index = 7; byte_index >= 0; --byte_index) {
            value = (value << 8) | static_cast<unsigned char>(
                                       stream[i * 8 + static_cast<std::size_t>(byte_index)]);
        }
        values[i] = value;
    }
    return values;
}

}  // namespace

std::vector<std::uint64_t> derive_tensor_mask_stream(const std::string& shared_secret,
                                                     const std::string& purpose_label,
                                                     const std::string& canonical_context,
                                                     std::size_t element_count) {
    if (element_count == 0) {
        return {};
    }
    const auto purpose_key =
        derive_purpose_key(shared_secret, purpose_label, canonical_context, kChaCha20KeyLength);
    // A fixed, all-zero nonce is safe here specifically because
    // `purpose_key` is itself unique per (shared_secret, purpose,
    // canonical_context) -- HKDF already guarantees key uniqueness
    // across every distinct mask this protocol ever derives, so
    // ChaCha20 never reuses a (key, nonce) pair across two different
    // masks. Reusing a fixed nonce would only be unsafe if the *key*
    // could repeat for a different plaintext, which derive_purpose_key's
    // domain separation already rules out.
    const std::string zero_nonce(kChaCha20NonceLength, '\0');
    const std::size_t byte_count = element_count * 8;
    const auto keystream = chacha20_keystream(purpose_key, zero_nonce, 0, byte_count);
    return bytes_to_le_uint64_values(keystream, element_count);
}

std::vector<std::uint64_t> mask_tensor(const std::vector<std::int64_t>& encoded_tensor,
                                       const std::vector<PeerMaskStream>& peer_streams) {
    std::vector<std::uint64_t> masked(encoded_tensor.size());
    for (std::size_t i = 0; i < encoded_tensor.size(); ++i) {
        masked[i] = static_cast<std::uint64_t>(encoded_tensor[i]);
    }
    for (const auto& peer : peer_streams) {
        if (peer.mask_values.size() != encoded_tensor.size()) {
            throw std::invalid_argument(
                "mask_tensor: peer '" + peer.peer_participant_id +
                "' mask stream length does not match the tensor's element count");
        }
        for (std::size_t i = 0; i < encoded_tensor.size(); ++i) {
            masked[i] = apply_pairwise_mask(masked[i], peer.mask_values[i], peer.sign);
        }
    }
    return masked;
}

std::vector<std::uint64_t> sum_masked_tensors(
    const std::vector<std::vector<std::uint64_t>>& per_participant_masked_tensors) {
    if (per_participant_masked_tensors.empty()) {
        throw std::invalid_argument(
            "sum_masked_tensors: at least one participant's masked tensor is required");
    }
    const std::size_t element_count = per_participant_masked_tensors.front().size();
    for (const auto& tensor : per_participant_masked_tensors) {
        if (tensor.size() != element_count) {
            throw std::invalid_argument(
                "sum_masked_tensors: all participants' masked tensors must have the "
                "identical element count");
        }
    }
    std::vector<std::uint64_t> sum(element_count, 0);
    for (std::size_t i = 0; i < element_count; ++i) {
        std::vector<std::uint64_t> column(per_participant_masked_tensors.size());
        for (std::size_t p = 0; p < per_participant_masked_tensors.size(); ++p) {
            column[p] = per_participant_masked_tensors[p][i];
        }
        sum[i] = sum_masked_values(column);
    }
    return sum;
}

std::uint64_t derive_weight_mask(const std::string& shared_secret,
                                 const std::string& purpose_label,
                                 const std::string& canonical_context) {
    const auto values =
        derive_tensor_mask_stream(shared_secret, purpose_label, canonical_context, 1);
    return values.front();
}

}  // namespace fl::coordinator
