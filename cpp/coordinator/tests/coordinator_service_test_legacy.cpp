// Regression coverage for the CreateRun wire-mapping gap (see
// docs/create-run-wire-mapping.md): before this fix, CreateRunRequest had
// no wire representation for client_ids or training hyperparameters, so
// AcquireTask could never select a real client through the live gRPC
// path. This test drives CoordinatorServiceImpl through real gRPC message
// types (no mocks) to prove the fix end-to-end: CreateRun -> RegisterWorker
// -> StartRun -> AcquireTask actually returns a task built from the
// CreateRunRequest's client_ids/hyperparameters/model_manifest.
//
// Only compiled when gRPC/Protobuf are found (see cpp/CMakeLists.txt) —
// same constraint as coordinator_service.cpp itself.
#include "fl_coordinator/coordinator_service.hpp"
#include "fl_coordinator/replay_protection_store.hpp"
#include "fl_coordinator/security_event_journal.hpp"
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "fl_coordinator/signing_key_registry.hpp"
#include "fl_coordinator/worker_identity_registry.hpp"
#include "test_support.hpp"

#include <openssl/evp.h>

#include <chrono>
#include <cmath>
#include <filesystem>

namespace fl::coordinator::testing {

namespace {

// -- Ed25519 test helpers, duplicated from signed_envelope_verifier_test.cpp
// (same rationale as that file's own duplication of json_escape_string
// from capability_statement_verifier.cpp: each test file keeps its own
// small helpers rather than sharing a utility header). --

// CoordinatorServiceImpl::now_unix_s() reads the real wall clock (not
// an injectable test clock -- see coordinator_service.cpp), so any
// envelope this test signs must carry an issued_at/expires_at anchored
// to real "now", not an arbitrary small fixture value like 5000.0 (which
// verify_signed_envelope would immediately reject as expired against a
// real multi-billion-second Unix timestamp).
double real_now_unix_s() {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count()) /
           1000.0;
}

std::string hex_encode_bytes(const unsigned char* data, std::size_t length) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(length * 2);
    for (std::size_t i = 0; i < length; ++i) {
        out += kHex[(data[i] >> 4) & 0xF];
        out += kHex[data[i] & 0xF];
    }
    return out;
}

struct TestKeypair {
    std::string public_key_hex;
    EVP_PKEY* pkey = nullptr;
};

TestKeypair generate_test_keypair() {
    TestKeypair result;
    result.pkey = EVP_PKEY_Q_keygen(nullptr, nullptr, "ED25519");
    unsigned char raw_public[32];
    std::size_t raw_public_len = sizeof(raw_public);
    EVP_PKEY_get_raw_public_key(result.pkey, raw_public, &raw_public_len);
    result.public_key_hex = hex_encode_bytes(raw_public, raw_public_len);
    return result;
}

std::string sign_hex_for_test(EVP_PKEY* pkey, const std::string& message) {
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(ctx, nullptr, nullptr, nullptr, pkey);
    unsigned char signature[64];
    std::size_t signature_len = sizeof(signature);
    EVP_DigestSign(ctx,
                   signature,
                   &signature_len,
                   reinterpret_cast<const unsigned char*>(message.data()),
                   message.size());
    EVP_MD_CTX_free(ctx);
    return hex_encode_bytes(signature, signature_len);
}

// Builds a signed SubmitWorkerSecurityEventsRequest for `worker_id`,
// signed by `keypair` under `signing_key_id`, carrying `event_count`
// syntactically-valid events plus any extras appended by the caller via
// `augment`.
fl::coordinator::v1::SubmitWorkerSecurityEventsRequest make_security_event_batch_request(
    const std::string& worker_id,
    const std::string& signing_key_id,
    TestKeypair& keypair,
    int event_count,
    double issued_at,
    std::uint64_t sequence_number,
    const std::string& nonce) {
    fl::worker::v1::SignedWorkerSecurityEventBatch batch;
    batch.set_schema_version(1);
    batch.set_worker_id(worker_id);
    for (int i = 0; i < event_count; ++i) {
        auto* event = batch.add_events();
        event->set_schema_version(1);
        event->set_event_type("HEARTBEAT_ACCEPTED");
        event->set_severity("INFO");
        event->set_timestamp("2026-01-01T00:00:00Z");
        event->set_actor_type("WORKER");
        event->set_safe_actor_id(worker_id);
        event->set_subject_type("HEARTBEAT");
        event->set_safe_subject_id(worker_id);
        event->set_outcome("ACCEPTED");
    }

    const auto hash_result = security_event_batch_payload_hash_input(batch);
    fl::worker::v1::SignedWorkerEnvelope envelope;
    envelope.set_schema_version(1);
    envelope.set_message_type(
        fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURITY_EVENT_BATCH);
    envelope.set_worker_id(worker_id);
    envelope.set_message_stream(
        fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_SECURITY_EVENTS);
    envelope.set_sequence_number(sequence_number);
    envelope.set_issued_at(issued_at);
    envelope.set_expires_at(issued_at + 60.0);
    envelope.set_nonce(nonce);
    envelope.set_signing_key_id(signing_key_id);
    unsigned char digest[32];
    unsigned int digest_len = 0;
    EVP_MD_CTX* digest_ctx = EVP_MD_CTX_new();
    EVP_DigestInit_ex(digest_ctx, EVP_sha256(), nullptr);
    EVP_DigestUpdate(digest_ctx, hash_result.hash_input.data(), hash_result.hash_input.size());
    EVP_DigestFinal_ex(digest_ctx, digest, &digest_len);
    EVP_MD_CTX_free(digest_ctx);
    envelope.set_payload_hash(hex_encode_bytes(digest, digest_len));
    envelope.set_signature(sign_hex_for_test(keypair.pkey, envelope_signing_bytes(envelope)));

    fl::coordinator::v1::SubmitWorkerSecurityEventsRequest request;
    request.set_worker_id(worker_id);
    *request.mutable_batch() = batch;
    *request.mutable_envelope() = envelope;
    return request;
}

fl::coordinator::v1::CreateRunRequest make_wire_request(const std::string& run_id) {
    fl::coordinator::v1::CreateRunRequest request;
    request.mutable_config()->set_run_id(run_id);
    request.mutable_optimizer()->set_algorithm("fedavg");
    request.mutable_optimizer()->set_weighting("uniform");
    request.mutable_optimizer()->set_server_lr(1.0);
    request.set_target_clients_per_round(2);
    request.set_total_clients(2);
    request.set_max_rounds(2);
    request.set_minimum_valid_results(2);
    request.set_round_timeout_seconds(300);
    request.add_client_ids("client-a");
    request.add_client_ids("client-b");
    request.set_local_epochs(3);
    request.set_batch_size(16);
    request.set_learning_rate(0.05);
    request.set_momentum(0.9);
    request.set_weight_decay(1e-4);
    request.set_task_lease_seconds(60);
    request.set_max_task_retries(3);

    auto* manifest = request.mutable_model_manifest();
    manifest->set_model_id("toy");
    manifest->set_model_version("v0");
    auto* tensor = manifest->add_tensors();
    tensor->set_name("weight");
    tensor->add_shape(1);
    manifest->mutable_aggregation_manifest()->add_shared_parameter_names("weight");

    return request;
}

}  // namespace

void run_coordinator_service_tests() {
    std::filesystem::remove_all("coordinator_service_test_scratch");
    CoordinatorConfig coordinator_config;
    RunManager manager(coordinator_config,
                       "coordinator_service_test_scratch/checkpoints",
                       "coordinator_service_test_scratch/scaffold");
    CoordinatorServiceImpl service(manager);

    // --- The real bug: client_ids/hyperparameters/manifest must reach AcquireTask ---
    {
        auto request = make_wire_request("run-wire-mapping");
        fl::coordinator::v1::CreateRunResponse create_response;
        auto create_status = service.CreateRun(nullptr, &request, &create_response);
        check(create_status.ok(),
              "CreateRun with client_ids/manifest is accepted: " + create_status.error_message());

        fl::worker::v1::RegisterWorkerRequest register_request;
        register_request.set_worker_id("worker-a");
        fl::worker::v1::RegisterWorkerResponse register_response;
        check(service.RegisterWorker(nullptr, &register_request, &register_response).ok(),
              "RegisterWorker succeeds");

        fl::coordinator::v1::StartRunRequest start_request;
        start_request.set_run_id("run-wire-mapping");
        fl::coordinator::v1::RunStateResponse start_response;
        check(service.StartRun(nullptr, &start_request, &start_response).ok(), "StartRun succeeds");

        fl::coordinator::v1::AcquireTaskRequest acquire_request;
        acquire_request.set_worker_id("worker-a");
        acquire_request.set_run_id("run-wire-mapping");
        fl::coordinator::v1::ClientTrainingTask task_response;
        check(service.AcquireTask(nullptr, &acquire_request, &task_response).ok(),
              "AcquireTask succeeds");
        check(task_response.task_available(),
              "AcquireTask returns a real task (previously impossible: client_ids never reached "
              "RunConfig over gRPC)");
        check(task_response.task().client_id() == "client-a" ||
                  task_response.task().client_id() == "client-b",
              "acquired task's client_id comes from CreateRunRequest.client_ids");
        check(task_response.local_epochs() == 3,
              "acquired task carries local_epochs from the wire request");
        check(task_response.batch_size() == 16,
              "acquired task carries batch_size from the wire request");
        check(std::abs(task_response.learning_rate() - 0.05) < 1e-9,
              "acquired task carries learning_rate from the wire request");

        // The tensor-transport gap: SubmitClientResult previously never
        // decoded result.tensor_manifest() into submission.update.delta at
        // all, so it stayed empty regardless of what a worker submitted —
        // see docs/create-run-wire-mapping.md's "tensor transport"
        // section. A real accepted delta for a declared shared tensor
        // proves the decode path is wired.
        fl::coordinator::v1::SubmitClientResultRequest submit_request;
        submit_request.set_worker_id("worker-a");
        submit_request.set_task_id(task_response.task_id());
        submit_request.set_lease_id(task_response.lease_id());
        auto* result = submit_request.mutable_result();
        result->set_run_id("run-wire-mapping");
        result->set_round_id(task_response.task().round_id());
        result->set_client_id(task_response.task().client_id());
        result->set_base_model_version(task_response.task().model_version());
        result->set_sample_count(4);
        result->set_algorithm("fedavg");
        result->set_nonce("nonce-1");
        auto* tensor = result->add_tensor_manifest();
        tensor->set_name("weight");
        tensor->add_shape(1);
        tensor->add_values(2.0);

        fl::coordinator::v1::SubmitClientResultResponse submit_response;
        check(service.SubmitClientResult(nullptr, &submit_request, &submit_response).ok(),
              "SubmitClientResult RPC succeeds");
        check(submit_response.accepted(),
              "a real delta tensor ('weight', declared shared) is accepted");
    }

    // --- Proves tensor_manifest decoding is real, not the previous
    // always-empty no-op: a delta tensor the aggregation manifest marks
    // personalized-only must be rejected. Before the fix, delta stayed
    // empty regardless of what was submitted, so this rejection path
    // could never trigger no matter what a worker sent. ---
    {
        auto request = make_wire_request("run-personalized-rejection");
        auto* aggregation_manifest =
            request.mutable_model_manifest()->mutable_aggregation_manifest();
        aggregation_manifest->clear_shared_parameter_names();
        aggregation_manifest->add_personalized_parameter_names("weight");

        fl::coordinator::v1::CreateRunResponse create_response;
        check(service.CreateRun(nullptr, &request, &create_response).ok(),
              "CreateRun with a personalized-only manifest is accepted");

        fl::worker::v1::RegisterWorkerRequest register_request;
        register_request.set_worker_id("worker-b");
        fl::worker::v1::RegisterWorkerResponse register_response;
        check(service.RegisterWorker(nullptr, &register_request, &register_response).ok(),
              "RegisterWorker succeeds");

        fl::coordinator::v1::StartRunRequest start_request;
        start_request.set_run_id("run-personalized-rejection");
        fl::coordinator::v1::RunStateResponse start_response;
        check(service.StartRun(nullptr, &start_request, &start_response).ok(), "StartRun succeeds");

        fl::coordinator::v1::AcquireTaskRequest acquire_request;
        acquire_request.set_worker_id("worker-b");
        acquire_request.set_run_id("run-personalized-rejection");
        fl::coordinator::v1::ClientTrainingTask task_response;
        check(service.AcquireTask(nullptr, &acquire_request, &task_response).ok(),
              "AcquireTask succeeds");
        check(task_response.task_available(), "a task is available to submit against");

        fl::coordinator::v1::SubmitClientResultRequest submit_request;
        submit_request.set_worker_id("worker-b");
        submit_request.set_task_id(task_response.task_id());
        submit_request.set_lease_id(task_response.lease_id());
        auto* result = submit_request.mutable_result();
        result->set_run_id("run-personalized-rejection");
        result->set_round_id(task_response.task().round_id());
        result->set_client_id(task_response.task().client_id());
        result->set_base_model_version(task_response.task().model_version());
        result->set_sample_count(4);
        result->set_algorithm("fedavg");
        result->set_nonce("nonce-2");
        auto* tensor = result->add_tensor_manifest();
        tensor->set_name("weight");
        tensor->add_shape(1);
        tensor->add_values(2.0);

        fl::coordinator::v1::SubmitClientResultResponse submit_response;
        check(service.SubmitClientResult(nullptr, &submit_request, &submit_response).ok(),
              "SubmitClientResult RPC succeeds (RPC-level success; rejection is at the domain "
              "level via `accepted`)");
        check(!submit_response.accepted(),
              "a decoded 'weight' delta is rejected as personalized-only (proves "
              "tensor_manifest() actually reached submission.update.delta)");
    }

    // --- Required-field / enum validation added alongside the mapping fix ---
    {
        auto request = make_wire_request("");
        fl::coordinator::v1::CreateRunResponse response;
        check(!service.CreateRun(nullptr, &request, &response).ok(), "empty run_id is rejected");
    }
    {
        auto request = make_wire_request("run-zero-clients");
        request.set_total_clients(0);
        fl::coordinator::v1::CreateRunResponse response;
        check(!service.CreateRun(nullptr, &request, &response).ok(),
              "zero total_clients is rejected");
    }
    {
        auto request = make_wire_request("run-oversized-target");
        request.set_target_clients_per_round(10);
        fl::coordinator::v1::CreateRunResponse response;
        check(!service.CreateRun(nullptr, &request, &response).ok(),
              "target_clients_per_round exceeding total_clients is rejected");
    }
    {
        auto request = make_wire_request("run-bad-algorithm");
        request.mutable_optimizer()->set_algorithm("not-a-real-algorithm");
        fl::coordinator::v1::CreateRunResponse response;
        check(!service.CreateRun(nullptr, &request, &response).ok(),
              "unknown algorithm is rejected, not silently treated as FedAvg");
    }
    {
        auto request = make_wire_request("run-bad-weighting");
        request.mutable_optimizer()->set_weighting("not-a-real-weighting");
        fl::coordinator::v1::CreateRunResponse response;
        check(!service.CreateRun(nullptr, &request, &response).ok(),
              "unknown weighting is rejected, not silently treated as uniform");
    }

    // --- Privacy Engineering phase: hybrid DP end-to-end through real
    // gRPC message types — CreateRun's privacy_config wire mapping,
    // AcquireTask relaying sample_level_dp_active/sample_level_privacy
    // to a dispatched task, SubmitClientResult accepting a client's
    // sample_level_privacy ledger entry, and the three read-only
    // GetPrivacyMetrics/GetPrivacyLedger/GetPrivacyProjection RPCs all
    // reflecting that state. See docs/hybrid-dp.md. ---
    {
        auto request = make_wire_request("run-hybrid-grpc");
        auto* privacy = request.mutable_privacy_config();
        privacy->set_mode(fl::privacy::v1::PRIVACY_MODE_HYBRID_DP);
        privacy->mutable_sample_level()->set_noise_multiplier(0.9);
        privacy->mutable_sample_level()->set_max_grad_norm(1.2);
        privacy->mutable_sample_level()->set_target_delta(1e-6);
        privacy->mutable_sample_level()->set_accountant(fl::privacy::v1::ACCOUNTANT_TYPE_RDP);
        privacy->mutable_user_level()->set_noise_multiplier(1.0);
        privacy->mutable_user_level()->set_initial_clipping_bound(10.0);
        privacy->mutable_user_level()->set_target_delta(1e-5);
        privacy->mutable_user_level()->set_epsilon_budget(50.0);

        fl::coordinator::v1::CreateRunResponse create_response;
        check(service.CreateRun(nullptr, &request, &create_response).ok(),
              "CreateRun with a hybrid-DP privacy_config is accepted");

        // Compatible-worker-only task assignment (docs/worker-privacy-
        // capabilities.md): both workers must advertise
        // supports_sample_level_dp, or AcquireTask below correctly
        // refuses to hand them a task from this hybrid-DP run.
        fl::worker::v1::RegisterWorkerRequest register_request_a;
        register_request_a.set_worker_id("worker-hybrid-a");
        register_request_a.mutable_capability()->mutable_privacy()->set_supports_sample_level_dp(
            true);
        fl::worker::v1::RegisterWorkerResponse register_response_a;
        check(service.RegisterWorker(nullptr, &register_request_a, &register_response_a).ok(),
              "RegisterWorker succeeds (worker-hybrid-a)");
        fl::worker::v1::RegisterWorkerRequest register_request_b;
        register_request_b.set_worker_id("worker-hybrid-b");
        register_request_b.mutable_capability()->mutable_privacy()->set_supports_sample_level_dp(
            true);
        fl::worker::v1::RegisterWorkerResponse register_response_b;
        check(service.RegisterWorker(nullptr, &register_request_b, &register_response_b).ok(),
              "RegisterWorker succeeds (worker-hybrid-b)");

        fl::coordinator::v1::StartRunRequest start_request;
        start_request.set_run_id("run-hybrid-grpc");
        fl::coordinator::v1::RunStateResponse start_response;
        check(service.StartRun(nullptr, &start_request, &start_response).ok(), "StartRun succeeds");

        fl::coordinator::v1::AcquireTaskRequest acquire_request_a;
        acquire_request_a.set_worker_id("worker-hybrid-a");
        acquire_request_a.set_run_id("run-hybrid-grpc");
        fl::coordinator::v1::ClientTrainingTask task_response_a;
        check(service.AcquireTask(nullptr, &acquire_request_a, &task_response_a).ok(),
              "AcquireTask succeeds (worker-hybrid-a)");
        check(task_response_a.sample_level_dp_active(),
              "AcquireTask marks the dispatched task sample_level_dp_active under hybrid mode");
        check(std::abs(task_response_a.sample_level_privacy().noise_multiplier() - 0.9) < 1e-9,
              "dispatched task carries the CreateRun-configured sample-level noise_multiplier "
              "over the wire");

        fl::coordinator::v1::AcquireTaskRequest acquire_request_b;
        acquire_request_b.set_worker_id("worker-hybrid-b");
        acquire_request_b.set_run_id("run-hybrid-grpc");
        fl::coordinator::v1::ClientTrainingTask task_response_b;
        check(service.AcquireTask(nullptr, &acquire_request_b, &task_response_b).ok(),
              "AcquireTask succeeds (worker-hybrid-b)");

        const auto submit_for = [&](const std::string& worker_id,
                                    const fl::coordinator::v1::ClientTrainingTask& task_response,
                                    double sample_epsilon) {
            fl::coordinator::v1::SubmitClientResultRequest submit_request;
            submit_request.set_worker_id(worker_id);
            submit_request.set_task_id(task_response.task_id());
            submit_request.set_lease_id(task_response.lease_id());
            auto* result = submit_request.mutable_result();
            result->set_run_id("run-hybrid-grpc");
            result->set_round_id(task_response.task().round_id());
            result->set_client_id(task_response.task().client_id());
            result->set_base_model_version(task_response.task().model_version());
            result->set_sample_count(4);
            result->set_algorithm("fedavg");
            result->set_nonce("nonce-" + worker_id);
            auto* tensor = result->add_tensor_manifest();
            tensor->set_name("weight");
            tensor->add_shape(1);
            tensor->add_values(2.0);
            auto* sample_entry = submit_request.mutable_sample_level_privacy();
            sample_entry->set_run_id("run-hybrid-grpc");
            sample_entry->set_round_id(task_response.task().round_id());
            sample_entry->set_client_id(task_response.task().client_id());
            sample_entry->set_epsilon(sample_epsilon);
            sample_entry->set_delta(1e-6);
            sample_entry->set_noise_multiplier(0.9);
            sample_entry->set_sample_rate(0.25);
            sample_entry->set_steps(4);
            sample_entry->set_accountant(fl::privacy::v1::ACCOUNTANT_TYPE_RDP);

            fl::coordinator::v1::SubmitClientResultResponse submit_response;
            check(service.SubmitClientResult(nullptr, &submit_request, &submit_response).ok(),
                  "SubmitClientResult RPC succeeds (" + worker_id + ")");
            check(
                submit_response.accepted(),
                "the client's result (with sample_level_privacy) is accepted (" + worker_id + ")");
        };
        submit_for("worker-hybrid-a", task_response_a, /*sample_epsilon=*/1.5);
        submit_for("worker-hybrid-b", task_response_b, /*sample_epsilon=*/1.3);

        // Round finalization (clip -> aggregate -> noise -> ledger
        // entry) happens lazily inside RunInstance::advance(), which
        // AcquireTask calls before trying to hand out a task — there is
        // no separate "advance" RPC. Both clients already have accepted
        // results, so this call's real purpose is triggering that
        // advance(); the (round-2) task it returns is unused.
        fl::coordinator::v1::AcquireTaskRequest advance_request;
        advance_request.set_worker_id("worker-hybrid-a");
        advance_request.set_run_id("run-hybrid-grpc");
        fl::coordinator::v1::ClientTrainingTask advance_response;
        check(service.AcquireTask(nullptr, &advance_request, &advance_response).ok(),
              "AcquireTask (used here only to trigger round finalization) succeeds");

        fl::coordinator::v1::GetPrivacyMetricsRequest metrics_request;
        metrics_request.set_run_id("run-hybrid-grpc");
        fl::privacy::v1::PrivacyMetricsSnapshot metrics_response;
        check(service.GetPrivacyMetrics(nullptr, &metrics_request, &metrics_response).ok(),
              "GetPrivacyMetrics succeeds");
        check(metrics_response.has_sample_level() && metrics_response.sample_epsilon() == 1.5,
              "GetPrivacyMetrics reflects the submitted sample-level ledger entry");
        check(metrics_response.has_user_level() && metrics_response.user_epsilon() > 0.0,
              "GetPrivacyMetrics reflects the central user-level accountant, populated by the "
              "same round's aggregation");
        check(metrics_response.sample_epsilon() != metrics_response.user_epsilon(),
              "Critical Privacy Rule: sample-level and user-level epsilon are reported "
              "independently, never combined into one number");

        fl::coordinator::v1::GetPrivacyLedgerRequest ledger_request;
        ledger_request.set_run_id("run-hybrid-grpc");
        fl::coordinator::v1::GetPrivacyLedgerResponse ledger_response;
        check(service.GetPrivacyLedger(nullptr, &ledger_request, &ledger_response).ok(),
              "GetPrivacyLedger succeeds");
        check(ledger_response.sample_level_entries_size() == 2,
              "GetPrivacyLedger returns both submitted sample-level entries");
        check(ledger_response.sample_level_entries(0).client_id() ==
                  task_response_a.task().client_id(),
              "returned sample-level entry carries the correct client_id");
        check(ledger_response.user_level_entries_size() == 1,
              "GetPrivacyLedger returns the one completed round's user-level entry");
        check(ledger_response.clipping_entries_size() == 0,
              "adaptive clipping was not enabled for this run -> empty clipping ledger");

        fl::coordinator::v1::GetPrivacyProjectionRequest projection_request;
        projection_request.set_run_id("run-hybrid-grpc");
        fl::coordinator::v1::PrivacyProjection projection_response;
        check(service.GetPrivacyProjection(nullptr, &projection_request, &projection_response).ok(),
              "GetPrivacyProjection succeeds");
        check(projection_response.has_user_level() &&
                  projection_response.user_projected_next_epsilon() >
                      projection_response.user_current_epsilon(),
              "user-level projected-next-epsilon is strictly greater than current (one more "
              "round always costs additional budget)");
        check(std::abs(projection_response.user_budget_remaining() -
                       (50.0 - projection_response.user_current_epsilon())) < 1e-9,
              "user-level budget_remaining reflects the configured epsilon_budget=50.0");
        check(!projection_response.has_clipping(),
              "adaptive clipping wasn't enabled -> no clipping projection reported");

        // --- ListWorkers surfaces both workers' advertised privacy
        // capabilities (docs/worker-privacy-capabilities.md) — proves
        // the RegisterWorker -> WorkerRegistry -> ListWorkers round trip
        // for the field the Go control plane's capabilities endpoint
        // depends on. ---
        fl::coordinator::v1::ListWorkersRequest list_request;
        fl::coordinator::v1::ListWorkersResponse list_response;
        check(service.ListWorkers(nullptr, &list_request, &list_response).ok(),
              "ListWorkers succeeds");
        bool found_hybrid_a = false;
        for (const auto& worker : list_response.workers()) {
            if (worker.worker_id() == "worker-hybrid-a") {
                found_hybrid_a = true;
                check(worker.capability().privacy().supports_sample_level_dp(),
                      "ListWorkers reports worker-hybrid-a's advertised "
                      "supports_sample_level_dp=true");
            }
        }
        check(found_hybrid_a, "ListWorkers includes worker-hybrid-a among registered workers");
    }

    // --- A non-private run's privacy RPCs return an all-false/empty
    // response rather than erroring — Critical Privacy Rule's flip side:
    // absence of privacy must be just as visible/unambiguous as its
    // presence, never silently omitted from the API surface. ---
    {
        auto request = make_wire_request("run-non-private-grpc");
        fl::coordinator::v1::CreateRunResponse create_response;
        check(service.CreateRun(nullptr, &request, &create_response).ok(),
              "CreateRun without privacy_config (non-private) is accepted");

        fl::coordinator::v1::GetPrivacyMetricsRequest metrics_request;
        metrics_request.set_run_id("run-non-private-grpc");
        fl::privacy::v1::PrivacyMetricsSnapshot metrics_response;
        check(service.GetPrivacyMetrics(nullptr, &metrics_request, &metrics_response).ok(),
              "GetPrivacyMetrics succeeds even for a non-private run");
        check(!metrics_response.has_sample_level() && !metrics_response.has_user_level() &&
                  !metrics_response.has_clipping(),
              "a non-private run reports has_* = false for every mechanism");
    }

    // --- Security audit finding (docs/privacy-engineering-security-
    // audit.md, section 3): a submitted sample_level_privacy entry whose
    // embedded run_id/round_id/client_id doesn't match the outer,
    // lease-validated result must be rejected, not silently accepted
    // into the wrong run's/client's ledger. ---
    {
        auto request = make_wire_request("run-mismatched-entry");
        auto* privacy = request.mutable_privacy_config();
        privacy->set_mode(fl::privacy::v1::PRIVACY_MODE_SAMPLE_LEVEL_DP);
        privacy->mutable_sample_level()->set_noise_multiplier(0.9);
        privacy->mutable_sample_level()->set_max_grad_norm(1.2);
        privacy->mutable_sample_level()->set_target_delta(1e-6);

        fl::coordinator::v1::CreateRunResponse create_response;
        check(service.CreateRun(nullptr, &request, &create_response).ok(),
              "CreateRun with sample-level DP is accepted");

        fl::worker::v1::RegisterWorkerRequest register_request;
        register_request.set_worker_id("worker-mismatch");
        register_request.mutable_capability()->mutable_privacy()->set_supports_sample_level_dp(
            true);
        fl::worker::v1::RegisterWorkerResponse register_response;
        check(service.RegisterWorker(nullptr, &register_request, &register_response).ok(),
              "RegisterWorker succeeds");

        fl::coordinator::v1::StartRunRequest start_request;
        start_request.set_run_id("run-mismatched-entry");
        fl::coordinator::v1::RunStateResponse start_response;
        check(service.StartRun(nullptr, &start_request, &start_response).ok(), "StartRun succeeds");

        fl::coordinator::v1::AcquireTaskRequest acquire_request;
        acquire_request.set_worker_id("worker-mismatch");
        acquire_request.set_run_id("run-mismatched-entry");
        fl::coordinator::v1::ClientTrainingTask task_response;
        check(service.AcquireTask(nullptr, &acquire_request, &task_response).ok(),
              "AcquireTask succeeds");

        fl::coordinator::v1::SubmitClientResultRequest submit_request;
        submit_request.set_worker_id("worker-mismatch");
        submit_request.set_task_id(task_response.task_id());
        submit_request.set_lease_id(task_response.lease_id());
        auto* result = submit_request.mutable_result();
        result->set_run_id("run-mismatched-entry");
        result->set_round_id(task_response.task().round_id());
        result->set_client_id(task_response.task().client_id());
        result->set_base_model_version(task_response.task().model_version());
        result->set_sample_count(4);
        result->set_algorithm("fedavg");
        result->set_nonce("nonce-mismatch");
        auto* tensor = result->add_tensor_manifest();
        tensor->set_name("weight");
        tensor->add_shape(1);
        tensor->add_values(2.0);
        // Deliberately wrong client_id: doesn't match result->client_id() above.
        auto* sample_entry = submit_request.mutable_sample_level_privacy();
        sample_entry->set_run_id("run-mismatched-entry");
        sample_entry->set_round_id(task_response.task().round_id());
        sample_entry->set_client_id("client-that-did-not-submit-this-result");
        sample_entry->set_epsilon(1.0);
        sample_entry->set_delta(1e-6);

        fl::coordinator::v1::SubmitClientResultResponse submit_response;
        check(!service.SubmitClientResult(nullptr, &submit_request, &submit_response).ok(),
              "SubmitClientResult rejects (RPC-level error) a sample_level_privacy entry whose "
              "client_id doesn't match the result it was submitted alongside");
    }

    // --- Web Security Center, Event Centralization, and Security CI
    // slice: SubmitWorkerSecurityEvents end-to-end -- signature
    // verification, replay protection, unknown-worker rejection,
    // bounded batch size, per-event skip-not-fatal, and
    // GetSecurityEventSourceHealth reflecting the resulting aggregate
    // counters. A separate, fully-wired CoordinatorServiceImpl instance
    // (identity/signing-key/replay/journal all real, not the
    // everything-nullptr `service` instance used above) since none of
    // the tests above needed any of these stores. ---
    {
        std::filesystem::remove_all("coordinator_service_test_scratch/security_events");
        std::filesystem::create_directories("coordinator_service_test_scratch/security_events");

        CoordinatorConfig sec_config;
        RunManager sec_manager(sec_config,
                               "coordinator_service_test_scratch/security_events/checkpoints",
                               "coordinator_service_test_scratch/security_events/scaffold");
        WorkerIdentityRegistry identity_registry(
            "coordinator_service_test_scratch/security_events/identities.dat");
        SigningKeyRegistry signing_key_registry(
            "coordinator_service_test_scratch/security_events/signing_keys.dat");
        ReplayProtectionStore replay_store(
            "coordinator_service_test_scratch/security_events/replay.dat");
        SecurityEventJournal event_journal(
            "coordinator_service_test_scratch/security_events/events.jsonl");

        const double sec_test_now = real_now_unix_s();
        auto keypair = generate_test_keypair();
        identity_registry.register_identity("worker-sec-1",
                                            "cert-identity",
                                            "cert-serial",
                                            "fp-1",
                                            keypair.public_key_hex,
                                            "key-1",
                                            "1.0.0",
                                            "build-1",
                                            /*now_unix_s=*/sec_test_now,
                                            /*expires_at_unix_s=*/0.0);
        InitialSigningKeyRegistration initial_key;
        initial_key.worker_id = "worker-sec-1";
        initial_key.signing_key_id = "key-1";
        initial_key.public_key_hex = keypair.public_key_hex;
        initial_key.public_key_fingerprint = "fp-key-1";
        initial_key.now_unix_s = sec_test_now;
        initial_key.expires_at_unix_s = 0.0;
        initial_key.registration_source = "registration";
        signing_key_registry.register_initial_key(initial_key);

        CoordinatorServiceImpl sec_service(sec_manager,
                                           &identity_registry,
                                           &replay_store,
                                           /*allow_unsigned_client_results=*/true,
                                           nullptr,
                                           /*allow_unsigned_privacy_records=*/true,
                                           &signing_key_registry,
                                           nullptr,
                                           nullptr,
                                           nullptr,
                                           nullptr,
                                           "",
                                           "",
                                           "coordinator",
                                           TransportMode::kInsecureDevelopment,
                                           &event_journal,
                                           nullptr);

        // Accepted batch.
        auto accepted_request = make_security_event_batch_request("worker-sec-1",
                                                                  "key-1",
                                                                  keypair,
                                                                  /*event_count=*/2,
                                                                  /*issued_at=*/sec_test_now,
                                                                  /*sequence_number=*/1,
                                                                  "nonce-batch-1");
        fl::coordinator::v1::SubmitWorkerSecurityEventsResponse accepted_response;
        const auto accepted_status =
            sec_service.SubmitWorkerSecurityEvents(nullptr, &accepted_request, &accepted_response);
        check(accepted_status.ok(),
              "SubmitWorkerSecurityEvents RPC succeeds for a validly signed batch (got: " +
                  accepted_status.error_message() +
                  " / rejection_code=" + accepted_response.rejection_code() + ")");
        check(accepted_response.accepted(), "a validly signed, non-replayed batch is accepted");
        check(accepted_response.accepted_event_count() == 2,
              "both syntactically-valid events in the batch are accepted");
        check(accepted_response.rejected_event_count() == 0,
              "no events are rejected from a fully well-formed batch");
        check(event_journal.size() == 3,
              "the journal now holds the 2 relayed events plus 1 "
              "WORKER_SECURITY_EVENT_BATCH_ACCEPTED event the coordinator emits about the batch "
              "itself");

        // Replay: resubmitting the exact same envelope (same nonce/sequence) must be rejected.
        auto replay_request = accepted_request;
        fl::coordinator::v1::SubmitWorkerSecurityEventsResponse replay_response;
        const auto replay_status =
            sec_service.SubmitWorkerSecurityEvents(nullptr, &replay_request, &replay_response);
        check(!replay_status.ok(), "resubmitting an already-accepted batch envelope is rejected");
        check(!replay_response.rejection_code().empty(),
              "a replayed batch carries a non-empty rejection_code");
        check(event_journal.size() == 3,
              "a rejected replay does not journal any additional events");

        // Unknown worker.
        auto unknown_worker_keypair = generate_test_keypair();
        auto unknown_worker_request =
            make_security_event_batch_request("worker-that-never-registered",
                                              "key-1",
                                              unknown_worker_keypair,
                                              1,
                                              sec_test_now,
                                              1,
                                              "nonce-unknown");
        fl::coordinator::v1::SubmitWorkerSecurityEventsResponse unknown_worker_response;
        check(!sec_service
                   .SubmitWorkerSecurityEvents(
                       nullptr, &unknown_worker_request, &unknown_worker_response)
                   .ok(),
              "a batch from an unregistered worker_id is rejected");
        check(unknown_worker_response.rejection_code() == "unknown_worker",
              "unknown-worker rejection carries the stable code unknown_worker");
        EVP_PKEY_free(unknown_worker_keypair.pkey);

        // Oversized batch (kMaxSecurityEventBatchSize == 200): must be
        // rejected wholesale, not truncated.
        auto oversized_request = make_security_event_batch_request("worker-sec-1",
                                                                   "key-1",
                                                                   keypair,
                                                                   /*event_count=*/201,
                                                                   /*issued_at=*/sec_test_now,
                                                                   /*sequence_number=*/2,
                                                                   "nonce-oversized");
        fl::coordinator::v1::SubmitWorkerSecurityEventsResponse oversized_response;
        check(!sec_service
                   .SubmitWorkerSecurityEvents(nullptr, &oversized_request, &oversized_response)
                   .ok(),
              "a batch exceeding the maximum event count is rejected wholesale");
        check(oversized_response.rejection_code() == "batch_too_large",
              "oversized-batch rejection carries the stable code batch_too_large");
        check(oversized_response.accepted_event_count() == 0,
              "a wholesale-rejected batch accepts none of its events, not a truncated subset");

        // Individual malformed event within an otherwise-valid,
        // correctly-signed batch: skipped, not fatal to the batch.
        {
            fl::worker::v1::SignedWorkerSecurityEventBatch batch;
            batch.set_schema_version(1);
            batch.set_worker_id("worker-sec-1");
            auto* good_event = batch.add_events();
            good_event->set_schema_version(1);
            good_event->set_event_type("HEARTBEAT_ACCEPTED");
            good_event->set_severity("INFO");
            good_event->set_actor_type("WORKER");
            good_event->set_subject_type("HEARTBEAT");
            good_event->set_outcome("ACCEPTED");
            auto* bad_event = batch.add_events();
            bad_event->set_schema_version(1);
            bad_event->set_event_type("NOT_A_REAL_EVENT_TYPE");
            bad_event->set_severity("INFO");
            bad_event->set_actor_type("WORKER");
            bad_event->set_subject_type("HEARTBEAT");
            bad_event->set_outcome("ACCEPTED");

            const auto hash_result = security_event_batch_payload_hash_input(batch);
            fl::worker::v1::SignedWorkerEnvelope envelope;
            envelope.set_schema_version(1);
            envelope.set_message_type(
                fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURITY_EVENT_BATCH);
            envelope.set_worker_id("worker-sec-1");
            envelope.set_message_stream(
                fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_SECURITY_EVENTS);
            envelope.set_sequence_number(3);
            envelope.set_issued_at(sec_test_now);
            envelope.set_expires_at(sec_test_now + 60.0);
            envelope.set_nonce("nonce-mixed");
            envelope.set_signing_key_id("key-1");
            unsigned char digest[32];
            unsigned int digest_len = 0;
            EVP_MD_CTX* digest_ctx = EVP_MD_CTX_new();
            EVP_DigestInit_ex(digest_ctx, EVP_sha256(), nullptr);
            EVP_DigestUpdate(
                digest_ctx, hash_result.hash_input.data(), hash_result.hash_input.size());
            EVP_DigestFinal_ex(digest_ctx, digest, &digest_len);
            EVP_MD_CTX_free(digest_ctx);
            envelope.set_payload_hash(hex_encode_bytes(digest, digest_len));
            envelope.set_signature(
                sign_hex_for_test(keypair.pkey, envelope_signing_bytes(envelope)));

            fl::coordinator::v1::SubmitWorkerSecurityEventsRequest mixed_request;
            mixed_request.set_worker_id("worker-sec-1");
            *mixed_request.mutable_batch() = batch;
            *mixed_request.mutable_envelope() = envelope;

            fl::coordinator::v1::SubmitWorkerSecurityEventsResponse mixed_response;
            check(sec_service.SubmitWorkerSecurityEvents(nullptr, &mixed_request, &mixed_response)
                      .ok(),
                  "a batch with one malformed event alongside a valid one still succeeds at the "
                  "RPC level (the batch's own signature is valid)");
            check(mixed_response.accepted(),
                  "a batch-level accept happens even when an individual event within it is "
                  "malformed");
            check(mixed_response.accepted_event_count() == 1,
                  "exactly the one well-formed event is accepted");
            check(mixed_response.rejected_event_count() == 1,
                  "the one malformed event (unrecognized event_type) is skipped, counted as "
                  "rejected, and does not fail the whole batch");
        }

        // GetSecurityEventSourceHealth reflects the aggregate
        // batch-acceptance counters this test's submissions produced.
        fl::coordinator::v1::GetSecurityEventSourceHealthRequest health_request;
        fl::coordinator::v1::GetSecurityEventSourceHealthResponse health_response;
        check(sec_service.GetSecurityEventSourceHealth(nullptr, &health_request, &health_response)
                  .ok(),
              "GetSecurityEventSourceHealth RPC succeeds");
        bool found_python_worker_source = false;
        for (const auto& source : health_response.sources()) {
            if (source.source_service() == "python-worker") {
                found_python_worker_source = true;
                check(source.batches_accepted() == 2,
                      "python-worker source health reports 2 accepted batches (the initial "
                      "accepted batch and the mixed-validity batch)");
                check(source.batches_rejected() == 3,
                      "python-worker source health reports 3 rejected batches (replay, oversized, "
                      "and unknown-worker -- record_rejection() increments the shared counter on "
                      "every rejection path, including unknown_worker, since the counter is a "
                      "process-wide aggregate, not attributed per worker_id)");
                check(source.distinct_workers_seen() == 1,
                      "python-worker source health reports exactly 1 distinct worker_id seen "
                      "(worker-sec-1) -- the unknown-worker attempt never reaches the "
                      "distinct-workers tracking");
            }
        }
        check(found_python_worker_source,
              "GetSecurityEventSourceHealth includes a python-worker source entry");

        EVP_PKEY_free(keypair.pkey);
    }
}

}  // namespace fl::coordinator::testing

int main() {
    fl::coordinator::testing::run_coordinator_service_tests();
    return fl::coordinator::testing::g_failures == 0 ? 0 : 1;
}
