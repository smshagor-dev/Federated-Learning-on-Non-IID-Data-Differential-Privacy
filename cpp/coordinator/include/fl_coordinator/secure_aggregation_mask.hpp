#pragma once

// Pairwise mask sign rule and ring arithmetic for the Secure
// Aggregation Protocol Foundation and No-Dropout Masked-Sum Core slice
// -- Work Packages R, T, U. See docs/pairwise-masking.md.
//
// Deliberately split from secure_aggregation_crypto.hpp (gRPC-gated,
// OpenSSL-backed): *generating* pairwise mask key stream bytes needs a
// vetted cryptographic pseudorandom generator (ChaCha20, via OpenSSL --
// see docs/secure-aggregation-cryptographic-provider.md), which only
// builds in the gRPC-gated coordinator target on this development
// machine. *Combining* an already-generated mask value with an encoded
// tensor value in the ring is pure `std::uint64_t` arithmetic with no
// cryptographic dependency at all, so it lives here, in the
// non-gRPC-gated fl_coordinator library, fully unit-testable on this
// Windows/MSVC machine without Docker.
//
// Ring: modulo 2^64, matching secure_aggregation_encoding.hpp -- see
// that header for why native unsigned-integer wraparound is used
// directly as the ring's addition operator instead of a hand-rolled
// modular-reduction step.

#include <cstdint>
#include <string>
#include <vector>

namespace fl::coordinator {

// Canonical, locale-independent, byte-wise ordering of two participant
// identity strings (Work Package R: "no locale-sensitive ordering, no
// case-normalization ambiguity"). Deliberately a plain
// std::string::compare (ordinal byte comparison) -- never
// std::locale-aware comparison, never case-folded. Returns true if
// `a` sorts strictly before `b` under this ordering.
[[nodiscard]] bool participant_sorts_before(const std::string& a, const std::string& b);

// One deterministic cancellation rule, stated once and used
// everywhere in both languages (Work Package R): for a canonically-
// ordered pair (a, b) with `a` sorting before `b`, the lower-ordered
// participant *adds* the pairwise mask to its own contribution and the
// higher-ordered participant *subtracts* the same mask value from its
// own contribution. Summed across the complete, correctly-ordered
// cohort, every pairwise mask value is added exactly once and
// subtracted exactly once, so the total contribution of all pairwise
// masks to the aggregate sum is exactly zero in the ring -- this is
// the whole cancellation property the "no-dropout masked-sum" protocol
// depends on, and is proven by a dedicated cancellation test, not
// merely asserted here.
enum class PairwiseMaskSign {
    kAdd,
    kSubtract,
};

// Resolves which sign `self_participant_id` uses when masking against
// `peer_participant_id`, per the rule above. Throws
// std::invalid_argument if the two identities are equal (Work Package
// R: "duplicate participant rejection" -- a participant never derives
// a pairwise mask against itself, so equal identities are a caller
// error, not a valid input this function silently resolves).
[[nodiscard]] PairwiseMaskSign resolve_pairwise_mask_sign(const std::string& self_participant_id,
                                                           const std::string& peer_participant_id);

// Applies one pairwise mask value to a ring accumulator: `accumulator
// + mask` (kAdd) or `accumulator - mask` (kSubtract), both exactly
// std::uint64_t wraparound arithmetic (defined-behavior modulo-2^64
// addition/subtraction, matching secure_aggregation_encoding.hpp's
// ring choice).
[[nodiscard]] std::uint64_t apply_pairwise_mask(std::uint64_t accumulator, std::uint64_t mask,
                                                 PairwiseMaskSign sign);

// Combines a base encoded value with every pairwise mask this
// participant computed against every other participant in the frozen
// cohort (one mask value per peer, already sign-resolved by the
// caller via resolve_pairwise_mask_sign/apply_pairwise_mask, or passed
// here as raw (mask, sign) pairs for convenience). This is the
// participant-local step of Work Package T's "combine all pairwise
// masks" / "add the combined mask to the encoded tensor" -- it does
// not itself generate any mask bytes (see secure_aggregation_crypto.hpp
// for that).
struct SignedMask {
    std::uint64_t mask = 0;
    PairwiseMaskSign sign = PairwiseMaskSign::kAdd;
};

[[nodiscard]] std::uint64_t mask_encoded_value(std::int64_t base_encoded_value,
                                                const std::vector<SignedMask>& pairwise_masks);

// Sums a set of already-masked ring values (e.g. every frozen
// participant's masked contribution for one tensor element) into one
// accumulator -- exactly ring addition, repeated. Provided as a named
// function (rather than inlined at every call site) so
// "masked summation" per Work Package Z has one canonical
// implementation to test and reference.
[[nodiscard]] std::uint64_t sum_masked_values(const std::vector<std::uint64_t>& masked_values);

}  // namespace fl::coordinator
