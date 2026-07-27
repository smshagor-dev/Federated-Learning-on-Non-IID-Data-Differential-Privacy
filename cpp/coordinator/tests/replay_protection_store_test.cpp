#include "fl_coordinator/replay_protection_store.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>
#include <sstream>

namespace fl::coordinator::testing {

void run_replay_protection_store_tests(const std::string& scratch_dir) {
    using fl::coordinator::MessageStream;
    using fl::coordinator::ReplayCandidate;
    using fl::coordinator::ReplayProtectionStore;
    using fl::coordinator::ReplayProtectionStoreError;
    using fl::coordinator::ReplayRejectionReason;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/replay_store.dat";

    auto make_candidate = [](const std::string& worker_id, const std::string& key_id,
                             MessageStream stream, std::uint64_t sequence,
                             const std::string& nonce, double now) {
        ReplayCandidate candidate;
        candidate.worker_id = worker_id;
        candidate.signing_key_id = key_id;
        candidate.message_stream = stream;
        candidate.sequence_number = sequence;
        candidate.nonce = nonce;
        candidate.now_unix_s = now;
        candidate.nonce_retention_seconds = 3600.0;
        return candidate;
    };

    {
        ReplayProtectionStore store(store_path);

        const auto first =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 1, "nonce-1", 100.0);
        const auto decision = store.validate(first);
        check(decision.accepted, "the documented starting sequence value (1) is accepted for a new track");
        store.commit(first);

        const auto zero_seq =
            make_candidate("worker-2", "key-1", MessageStream::kHeartbeat, 0, "nonce-x", 100.0);
        check(!store.validate(zero_seq).accepted, "sequence_number 0 is rejected for a brand-new track");
    }

    {
        ReplayProtectionStore store(store_path);
        const auto second =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 2, "nonce-2", 101.0);
        check(store.validate(second).accepted, "sequence 2 following committed sequence 1 is accepted");
        store.commit(second);

        const auto duplicate_seq =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 2, "nonce-3", 102.0);
        const auto dup_decision = store.validate(duplicate_seq);
        check(!dup_decision.accepted, "a repeated sequence number is rejected");
        check(dup_decision.reason == ReplayRejectionReason::kDuplicateSequence,
              "a repeated sequence number is reported as kDuplicateSequence");

        const auto lower_seq =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 1, "nonce-4", 102.0);
        const auto lower_decision = store.validate(lower_seq);
        check(!lower_decision.accepted, "a lower sequence number is rejected");
        check(lower_decision.reason == ReplayRejectionReason::kLowerSequence,
              "a lower sequence number is reported as kLowerSequence");

        const auto duplicate_nonce =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 3, "nonce-2", 103.0);
        const auto nonce_decision = store.validate(duplicate_nonce);
        check(!nonce_decision.accepted, "a nonce reused within its retention window is rejected");
        check(nonce_decision.reason == ReplayRejectionReason::kDuplicateNonce,
              "a reused nonce is reported as kDuplicateNonce");

        const auto huge_gap =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 5000, "nonce-5", 103.0);
        const auto gap_decision = store.validate(huge_gap);
        check(!gap_decision.accepted, "a sequence gap beyond max_sequence_gap is rejected");
        check(gap_decision.reason == ReplayRejectionReason::kSequenceGapExceeded,
              "an excessive gap is reported as kSequenceGapExceeded");
    }

    {
        // Independent tracks: a different signing key, a different
        // stream, and a different worker_id must not interfere with
        // worker-1/key-1/HEARTBEAT's sequence state at all.
        ReplayProtectionStore store(store_path);
        const auto other_key =
            make_candidate("worker-1", "key-2", MessageStream::kHeartbeat, 1, "nonce-6", 104.0);
        check(store.validate(other_key).accepted,
              "a different signing key for the same worker/stream starts its own independent track at sequence 1");

        const auto other_stream =
            make_candidate("worker-1", "key-1", MessageStream::kClientResult, 1, "nonce-7", 104.0);
        check(store.validate(other_stream).accepted,
              "a different message stream for the same worker/key starts its own independent track at sequence 1");
    }

    {
        // Restart persistence: sequence 2 was committed above for
        // worker-1/key-1/HEARTBEAT; reopening the store must remember
        // it, rejecting sequence 2 again and accepting sequence 3.
        ReplayProtectionStore store(store_path);
        const auto repeat =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 2, "nonce-8", 105.0);
        check(!store.validate(repeat).accepted,
              "sequence state survives reopening the store from disk");

        const auto next =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 3, "nonce-9", 105.0);
        check(store.validate(next).accepted, "sequence 3 is accepted after reloading from disk");
        store.commit(next);
    }

    {
        // Nonce expiry: a nonce past its retention window is no longer
        // treated as a duplicate (purge_expired removes it), but the
        // sequence-number protection is completely independent and
        // still rejects sequence 3 again regardless.
        ReplayProtectionStore store(store_path);
        store.purge_expired(/*now_unix_s=*/999999.0);  // far past every nonce's retention window

        const auto reused_after_expiry =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 4, "nonce-2", 1000000.0);
        check(store.validate(reused_after_expiry).accepted,
              "an expired nonce hash no longer blocks reuse of that nonce string");

        const auto old_sequence_after_purge =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 3, "nonce-unique",
                           1000000.0);
        check(!store.validate(old_sequence_after_purge).accepted,
              "purge_expired never resets sequence-number protection, only nonce retention");
    }

    {
        // Worker-revocation cleanup: purge_worker removes every track
        // for that worker_id, across all signing keys and streams.
        ReplayProtectionStore store(store_path);
        store.purge_worker("worker-1");
        const auto fresh_after_purge =
            make_candidate("worker-1", "key-1", MessageStream::kHeartbeat, 1, "nonce-fresh",
                           2000000.0);
        check(store.validate(fresh_after_purge).accepted,
              "purge_worker resets a worker's tracks back to a fresh state (sequence 1 accepted again)");
    }

    {
        // Corruption detection.
        const std::string corrupt_path = scratch_dir + "/corrupt_replay_store.dat";
        {
            ReplayProtectionStore store(corrupt_path);
            store.commit(
                make_candidate("worker-x", "key-x", MessageStream::kControl, 1, "n", 0.0));
        }
        {
            std::ifstream in(corrupt_path, std::ios::binary);
            std::ostringstream buffer;
            buffer << in.rdbuf();
            std::string content = buffer.str();
            in.close();
            content.resize(content.size() / 2);
            std::ofstream out(corrupt_path, std::ios::binary | std::ios::trunc);
            out << content;
        }
        expect_throw([&]() { ReplayProtectionStore store(corrupt_path); },
                    "loading a truncated/corrupt replay protection store throws");
    }

    {
        // Masked Update Runtime and No-Dropout Secure FedAvg
        // Finalization slice: a real bug found live via this slice's
        // own 3-worker Docker validation. kSecureAggregation (key
        // advertisement) and kSecureAggregationMaskedUpdate (masked
        // update submission) must be independent tracks for the same
        // worker/key, exactly like the generic "different message
        // stream" case above -- otherwise a worker's very first
        // masked-update submission (sequence 1 on its own local
        // counter) collides with its already-committed key
        // advertisement (also sequence 1 on a shared counter) and is
        // rejected as a replay, even though it is a genuinely new
        // message.
        const std::string secagg_scratch = scratch_dir + "/secure_aggregation_streams";
        std::filesystem::remove_all(secagg_scratch);
        std::filesystem::create_directories(secagg_scratch);
        ReplayProtectionStore store(secagg_scratch + "/store.dat");

        const auto key_advertisement =
            make_candidate("worker-1", "key-1", MessageStream::kSecureAggregation, 1, "adv-nonce", 0.0);
        check(store.validate(key_advertisement).accepted,
              "kSecureAggregation: sequence 1 is accepted for a fresh key-advertisement track");
        store.commit(key_advertisement);

        const auto masked_update =
            make_candidate("worker-1", "key-1", MessageStream::kSecureAggregationMaskedUpdate, 1,
                           "masked-nonce", 0.0);
        check(store.validate(masked_update).accepted,
              "kSecureAggregationMaskedUpdate: sequence 1 on this independent track is accepted even "
              "though kSecureAggregation already committed sequence 1 for the same worker/key -- the "
              "real collision this slice's live validation found and this test now guards against");
    }
}

}  // namespace fl::coordinator::testing
