#pragma once

// Tensor and weight mask generation -- Work Packages T and U. Combines
// the crypto primitives (secure_aggregation_crypto.hpp) with the pure
// ring-arithmetic pairwise-mask combinators
// (secure_aggregation_mask.hpp) into the actual per-tensor-element
// masking operation described by Work Package T's 10-step process:
// derive a purpose-specific key from a pairwise shared secret, expand
// it into a per-element pseudorandom mask stream via ChaCha20, then
// fold that stream into an encoded tensor using the canonical sign
// rule.
//
// gRPC-gated (needs secure_aggregation_crypto.hpp's OpenSSL-backed
// primitives) -- see that header's identical header comment for why.
// Weight masking (Work Package U) is not a separate implementation:
// a single scalar weight is masked by calling the same functions with
// an element count of 1 -- see mask_weight/derive_weight_mask below,
// which are thin, explicitly-named wrappers rather than a duplicated
// code path.

#include "fl_coordinator/secure_aggregation_mask.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace fl::coordinator {

// One peer's contribution to a tensor's mask: the pseudorandom mask
// value for every element (already expanded from that peer's ChaCha20
// keystream, one std::uint64_t per element, matching the encoded
// tensor's element count exactly) plus the sign this participant uses
// against that peer (Work Package R).
struct PeerMaskStream {
    std::string peer_participant_id;
    PairwiseMaskSign sign;
    std::vector<std::uint64_t> mask_values;
};

// Work Package T, steps 1-6 (key derivation and keystream expansion):
// derives a purpose-specific key from `shared_secret` (via
// derive_purpose_key) and expands it into exactly `element_count`
// pseudorandom std::uint64_t mask values using a single ChaCha20
// keystream -- 8 keystream bytes per element, interpreted as a
// little-endian std::uint64_t (the same byte order Python's
// struct.unpack("<Q", ...) produces, so both languages derive
// identical mask values from the identical shared secret and
// canonical context).
//
// `canonical_context` must uniquely and deterministically identify
// this specific mask (session, round, tensor identifier, and the
// canonically-ordered participant pair) -- constructing that string is
// the caller's responsibility (see secure_aggregation_crypto.hpp's
// derive_purpose_key doc comment for why).
[[nodiscard]] std::vector<std::uint64_t> derive_tensor_mask_stream(
    const std::string& shared_secret,
    const std::string& purpose_label,
    const std::string& canonical_context,
    std::size_t element_count);

// Work Package T, steps 7-9: combines an encoded tensor (one signed
// ring value per element, as produced by encode_value) with every
// peer's mask stream, applying each peer's sign-resolved mask to the
// corresponding element -- the tensor-level generalization of
// mask_encoded_value (secure_aggregation_mask.hpp) applied
// element-wise. Throws std::invalid_argument if any peer's
// mask_values length does not match encoded_tensor's length.
[[nodiscard]] std::vector<std::uint64_t> mask_tensor(
    const std::vector<std::int64_t>& encoded_tensor,
    const std::vector<PeerMaskStream>& peer_streams);

// Work Package Z's masked-sum step, generalized to a tensor: sums a
// set of already-masked tensors (one per frozen cohort participant)
// element-wise, entirely in ring arithmetic. Throws
// std::invalid_argument if the tensors are not all the same length, or
// if the input is empty (there is no meaningful "sum of zero
// participants' tensors" in this protocol -- an empty cohort is a
// caller error, not a valid degenerate case).
[[nodiscard]] std::vector<std::uint64_t> sum_masked_tensors(
    const std::vector<std::vector<std::uint64_t>>& per_participant_masked_tensors);

// Work Package U: weight masking is tensor masking with element_count
// == 1 -- named explicitly rather than requiring every caller to wrap
// a scalar in a one-element vector by hand. The returned single mask
// value is combined with an encoded weight using
// secure_aggregation_mask.hpp's existing mask_encoded_value (weight
// masking needs no separate combining function -- it is already
// exactly the scalar case that function handles).
[[nodiscard]] std::uint64_t derive_weight_mask(const std::string& shared_secret,
                                               const std::string& purpose_label,
                                               const std::string& canonical_context);

}  // namespace fl::coordinator
