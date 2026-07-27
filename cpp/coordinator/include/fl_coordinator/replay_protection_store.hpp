#pragma once

// Persistent replay protection and per-stream sequence validation --
// Message Authenticity Enforcement and Identity Lifecycle slice, Work
// Packages E/F. See docs/replay-protection.md and
// docs/message-sequences.md.
//
// Deliberately protobuf-free and gRPC-free (unlike
// capability_statement_verifier.hpp) so it builds and is unit-testable
// on this Windows/MSVC development machine like WorkerIdentityRegistry
// -- the same non-gRPC-gated fl_coordinator library. The gRPC-gated
// envelope-verification code (a follow-on file) constructs a
// ReplayCandidate from a real fl::worker::v1::SignedWorkerEnvelope and
// calls into this store; this header never sees that protobuf type.

#include <chrono>
#include <cstdint>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

// Mirrors fl.worker.v1.SignedWorkerEnvelope.MessageStream one-for-one
// -- kept as a plain C++ enum here (not the protobuf enum) to keep this
// header protobuf-free; the gRPC-gated verification code is
// responsible for the two-way mapping.
enum class MessageStream {
    kControl,
    kHeartbeat,
    kTaskLifecycle,
    kClientResult,
    kPrivacyRecord,
    kPersonalization,
    kKeyManagement,
    // Web Security Center, Event Centralization, and Security CI slice,
    // Work Package L: its own sequence track, independent of every
    // other stream -- a burst of queued security-event batches must
    // never be rejected as a "sequence gap" against an unrelated
    // stream's counter.
    kSecurityEvents,
    // Secure Aggregation Protocol Foundation and No-Dropout Masked-Sum
    // Core slice: key advertisements and masked-update submissions get
    // their own independent sequence track for the same reason
    // kSecurityEvents does -- a worker's secure-aggregation messages
    // must never be sequence-gap-rejected against its unrelated
    // heartbeat/client-result counters. Schema-only this pass: no RPC
    // handler constructs a ReplayCandidate on this stream yet (see
    // docs/secure-aggregation-protocol-foundation.md's Tier 2 scope) --
    // added now so the enum value and its wire-format string are fixed
    // ahead of that follow-on integration work, matching this
    // project's "no implicit/undocumented enum growth" convention.
    kSecureAggregation,
    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice: the independent track kSecureAggregation's own doc comment
    // above already called for -- a worker's key-advertisement sequence
    // counter and its masked-update sequence counter must never collide
    // on one shared track (a real bug found live: reusing kSecureAggregation
    // for SubmitMaskedClientUpdate made the worker's second-ever secure-
    // aggregation message on that track collide with its first, since
    // the worker's own local SequenceStateStore already tracks these as
    // two separate named tracks -- "secure_aggregation_key_advertisement"
    // vs "secure_aggregation_masked_update", see coordinator_client.py).
    kSecureAggregationMaskedUpdate,
};

std::string to_string(MessageStream stream);
MessageStream message_stream_from_string(const std::string& value);

enum class ReplayRejectionReason {
    kNone,
    kDuplicateNonce,
    kDuplicateSequence,
    kLowerSequence,
    kSequenceGapExceeded,
};

std::string to_string(ReplayRejectionReason reason);

class ReplayProtectionStoreError : public std::runtime_error {
  public:
    explicit ReplayProtectionStoreError(const std::string& what);
};

// One (worker_id, signing_key_id, message_stream) track's proposed next
// message. `nonce` is the raw nonce string from the envelope -- this
// store hashes it internally (FNV-1a, the same non-cryptographic
// checksum convention already used throughout this codebase for
// internal bookkeeping -- see worker_identity_registry.cpp) rather than
// retaining it verbatim; the actual authenticity guarantee for the
// nonce's value comes from the Ed25519 signature already covering it
// (verified by the caller before this store is ever consulted), not
// from anything this store does.
struct ReplayCandidate {
    std::string worker_id;
    std::string signing_key_id;
    MessageStream message_stream{MessageStream::kControl};
    // Documented starting value for a brand-new track is 1, not 0 --
    // see docs/message-sequences.md.
    std::uint64_t sequence_number{0};
    std::string nonce;
    double now_unix_s{0.0};
    // How long, from now_unix_s, this nonce should be retained for
    // duplicate detection. A candidate's own expires_at (from the
    // envelope) is the natural choice for a caller to pass here.
    double nonce_retention_seconds{0.0};
};

struct ReplayDecision {
    bool accepted = false;
    ReplayRejectionReason reason = ReplayRejectionReason::kNone;
    std::string detail;  // human-readable; always set, even when accepted ("ok")
};

// Restart-safe, bounded-memory replay and sequence protection. One
// persisted file per instance, atomic temp-file+rename writes -- same
// pattern as WorkerIdentityRegistry and AggregatorCheckpointStore.
//
// Bounded by construction (Work Package E's "do not use an unbounded
// map or set" requirement): at most kMaxTracks distinct
// (worker_id, signing_key_id, message_stream) tracks are retained (the
// least-recently-updated track is evicted to make room for a new one
// past that bound); at most kMaxNonceEntriesPerTrack recent nonce
// hashes are retained per track (oldest-first eviction), independent
// of purge_expired's time-based cleanup.
//
// Concrete class, not an abstract interface: this codebase's existing
// persistence classes (WorkerIdentityRegistry, AggregatorCheckpointStore)
// are all concrete, single-implementation classes, and there is
// exactly one implementation of this store needed today -- consistent
// with that established pattern rather than introducing an unused
// abstraction layer.
class ReplayProtectionStore {
  public:
    static constexpr std::size_t kMaxNonceEntriesPerTrack = 256;
    static constexpr std::size_t kMaxTracks = 10000;
    static constexpr std::uint64_t kDefaultMaxSequenceGap = 1000;

    // max_sequence_gap: the only configurable out-of-order/gap policy
    // this pass implements is "reject anything more than this many
    // sequence numbers ahead of the last accepted one" -- see
    // docs/message-sequences.md's "What is deferred" section for why a
    // more permissive configurable policy (e.g. accept-with-warning)
    // is not implemented this pass.
    explicit ReplayProtectionStore(std::string persistence_path,
                                   std::uint64_t max_sequence_gap = kDefaultMaxSequenceGap);

    // Read-only: does not mutate state. Callers must call commit()
    // separately, and only after the candidate has been fully accepted
    // by every other check (signature, expiry, worker status, domain
    // validation) -- see docs/message-sequences.md's transaction-
    // ordering note, matching Work Package D's "do not update replay
    // state permanently if domain processing fails" requirement.
    [[nodiscard]] ReplayDecision validate(const ReplayCandidate& candidate) const;

    // Records `candidate` as accepted -- advances the track's
    // last_sequence_number and retains the nonce hash. Callers must
    // have already called validate() on this exact candidate and
    // checked ReplayDecision::accepted; commit() does not re-validate.
    void commit(const ReplayCandidate& candidate);

    // Removes nonce hash entries whose retention window has passed.
    // Never removes a track's last_sequence_number -- sequence state
    // must be retained indefinitely (bounded only by kMaxTracks
    // eviction) so a replayed old sequence number is still rejected
    // long after its nonce entry would otherwise have expired.
    void purge_expired(double now_unix_s);

    // Work Package O: worker-revocation cleanup policy. Removes every
    // track (across all signing keys and streams) for `worker_id` --
    // called once a worker is REVOKED and can never send another
    // acceptable message under any signing key again, so retaining its
    // replay/sequence history no longer serves a purpose.
    void purge_worker(const std::string& worker_id);

  private:
    struct NonceEntry {
        std::uint64_t nonce_hash{0};
        double expires_at_unix_s{0.0};
    };
    struct Track {
        std::string worker_id;
        std::string signing_key_id;
        MessageStream message_stream{MessageStream::kControl};
        std::uint64_t last_sequence_number{0};
        std::vector<NonceEntry> recent_nonce_hashes;
        double updated_at_unix_s{0.0};
    };

    void persist() const;  // caller must hold mutex_

    mutable std::mutex mutex_;
    std::string persistence_path_;
    std::uint64_t max_sequence_gap_;
    std::map<std::string, Track> tracks_;  // keyed by a composite track key
};

}  // namespace fl::coordinator
