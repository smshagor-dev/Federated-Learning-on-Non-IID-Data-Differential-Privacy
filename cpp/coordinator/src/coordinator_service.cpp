#include "fl_coordinator/coordinator_service.hpp"

#include <chrono>
#include <thread>

namespace fl::coordinator {

namespace {

fl::core::AggregationAlgorithm algorithm_from_wire(const std::string& value) {
    if (value == "fedavg") return fl::core::AggregationAlgorithm::kFedAvg;
    if (value == "fedprox") return fl::core::AggregationAlgorithm::kFedProx;
    if (value == "scaffold") return fl::core::AggregationAlgorithm::kScaffold;
    if (value == "fedadagrad") return fl::core::AggregationAlgorithm::kFedAdagrad;
    if (value == "fedadam") return fl::core::AggregationAlgorithm::kFedAdam;
    if (value == "fedyogi") return fl::core::AggregationAlgorithm::kFedYogi;
    return fl::core::AggregationAlgorithm::kFedAvg;
}

fl::core::WeightingStrategyType weighting_from_wire(const std::string& value) {
    if (value == "sample_count") return fl::core::WeightingStrategyType::kSampleCount;
    if (value == "capped_sample_count") return fl::core::WeightingStrategyType::kCappedSampleCount;
    if (value == "normalized_bounded") return fl::core::WeightingStrategyType::kNormalizedBounded;
    return fl::core::WeightingStrategyType::kUniform;
}

RunConfig config_from_request(const fl::coordinator::v1::CreateRunRequest& request) {
    RunConfig config;
    config.run_id = request.config().run_id();
    config.algorithm = algorithm_from_wire(request.optimizer().algorithm());
    config.weighting = weighting_from_wire(request.optimizer().weighting());
    config.server_lr = request.optimizer().server_lr();
    config.beta1 = request.optimizer().beta1();
    config.beta2 = request.optimizer().beta2();
    config.tau = request.optimizer().tau();
    config.contribution_cap = request.optimizer().contribution_cap();
    config.target_clients_per_round = request.target_clients_per_round();
    config.total_clients = request.total_clients();
    config.max_rounds = request.max_rounds();
    config.round_timeout_seconds = request.round_timeout_seconds();
    config.minimum_valid_results = request.minimum_valid_results();
    config.client_selection_seed = request.client_selection_seed();
    // ModelManifest tensor definitions arrive out-of-band via
    // GetModelManifest / a future model-distribution RPC (Work Package
    // C); CreateRunRequest carries run configuration, not the manifest
    // itself, so config.manifest is populated by the caller separately
    // in a full implementation. Left as the zero-tensor default here.
    return config;
}

fl::coordinator::v1::RunStateResponse to_run_state_response(const RunSnapshot& snapshot) {
    fl::coordinator::v1::RunStateResponse response;
    response.set_run_id(snapshot.run_id);
    response.set_state(fl::core::to_string(snapshot.state));
    response.set_current_round(snapshot.current_round);
    response.set_model_version(snapshot.model_version);
    return response;
}

void to_run_details(const RunSnapshot& snapshot, fl::coordinator::v1::RunDetails* out) {
    out->set_run_id(snapshot.run_id);
    out->set_state(fl::core::to_string(snapshot.state));
    out->set_current_round(snapshot.current_round);
    out->set_max_rounds(snapshot.max_rounds);
    out->set_model_version(snapshot.model_version);
    out->set_algorithm(fl::core::to_string(snapshot.algorithm));
    out->set_registered_workers(static_cast<std::uint32_t>(snapshot.registered_workers));
    out->set_healthy_workers(static_cast<std::uint32_t>(snapshot.healthy_workers));
}

grpc::Status to_grpc_status(const std::exception& error) {
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
}

}  // namespace

CoordinatorServiceImpl::CoordinatorServiceImpl(RunManager& manager)
    : manager_(&manager), started_at_(std::chrono::steady_clock::now()) {}

double CoordinatorServiceImpl::now_unix_s() {
    return static_cast<double>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count()
    ) / 1000.0;
}

grpc::Status CoordinatorServiceImpl::CreateRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::CreateRunRequest* request,
    fl::coordinator::v1::CreateRunResponse* response
) {
    try {
        const auto run_id = manager_->create_run(config_from_request(*request), now_unix_s());
        response->set_run_id(run_id);
        response->set_state(fl::core::to_string(manager_->get(run_id).snapshot().state));
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::StartRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::StartRunRequest* request,
    fl::coordinator::v1::RunStateResponse* response
) {
    try {
        auto& run = manager_->get(request->run_id());
        run.start(request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::PauseRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::PauseRunRequest* request,
    fl::coordinator::v1::RunStateResponse* response
) {
    try {
        auto& run = manager_->get(request->run_id());
        run.pause(request->reason(), request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::ResumeRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::ResumeRunRequest* request,
    fl::coordinator::v1::RunStateResponse* response
) {
    try {
        auto& run = manager_->get(request->run_id());
        run.resume(request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::CancelRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::CancelRunRequest* request,
    fl::coordinator::v1::RunStateResponse* response
) {
    try {
        auto& run = manager_->get(request->run_id());
        run.cancel(request->reason(), request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetRunRequest* request,
    fl::coordinator::v1::RunDetails* response
) {
    try {
        const auto& run = manager_->get(request->run_id());
        to_run_details(run.snapshot(), response);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::RegisterWorker(
    grpc::ServerContext*,
    const fl::worker::v1::RegisterWorkerRequest* request,
    fl::worker::v1::RegisterWorkerResponse* response
) {
    try {
        WorkerCapability capability;
        capability.device = request->capability().device();
        capability.cpu_count = request->capability().cpu_count();
        capability.gpu_available = request->capability().gpu_available();
        capability.gpu_count = request->capability().gpu_count();
        capability.available_memory_bytes = request->capability().available_memory_bytes();
        const auto info = manager_->worker_registry().register_worker(
            request->worker_id(), capability, now_unix_s()
        );
        response->set_worker_id(info.worker_id);
        response->set_status(fl::worker::v1::WORKER_STATUS_REGISTERING);
        response->set_heartbeat_interval_seconds(10);
        response->set_task_poll_interval_seconds(2);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::Heartbeat(
    grpc::ServerContext*,
    const fl::worker::v1::WorkerHeartbeatRequest* request,
    fl::worker::v1::WorkerHeartbeatResponse* response
) {
    try {
        manager_->worker_registry().heartbeat(
            request->worker_id(), fl::coordinator::WorkerStatus::kIdle,
            request->current_task_id(), now_unix_s()
        );
        response->set_acknowledged(true);
        response->set_should_disconnect(false);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::AcquireTask(
    grpc::ServerContext*,
    const fl::coordinator::v1::AcquireTaskRequest* request,
    fl::coordinator::v1::ClientTrainingTask* response
) {
    try {
        auto& run = manager_->get(request->run_id());
        run.advance(now_unix_s());
        const auto task = run.acquire_task(request->worker_id(), now_unix_s());
        response->set_task_available(task.has_value());
        if (task.has_value()) {
            response->set_task_id(task->task_id);
            response->set_lease_id(task->lease_id);
            auto* wire_task = response->mutable_task();
            wire_task->set_run_id(task->descriptor.run_id);
            wire_task->set_round_id(task->descriptor.round_id);
            wire_task->set_client_id(task->descriptor.client_id);
            wire_task->set_model_version(task->descriptor.model_version);
            wire_task->set_algorithm(fl::core::to_string(task->descriptor.algorithm));
            wire_task->set_dataset_reference(task->descriptor.dataset_reference);
            response->set_local_epochs(task->descriptor.local_epochs);
            response->set_batch_size(task->descriptor.batch_size);
            response->set_learning_rate(task->descriptor.learning_rate);
            response->set_momentum(task->descriptor.momentum);
            response->set_weight_decay(task->descriptor.weight_decay);
            response->set_fedprox_mu(task->descriptor.fedprox_mu);
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::SubmitClientResult(
    grpc::ServerContext*,
    const fl::coordinator::v1::SubmitClientResultRequest* request,
    fl::coordinator::v1::SubmitClientResultResponse* response
) {
    try {
        auto& run = manager_->get(request->result().run_id());
        ClientResultSubmission submission;
        submission.update.run_id = request->result().run_id();
        submission.update.round_id = request->result().round_id();
        submission.update.client_id = request->result().client_id();
        submission.update.base_model_version = request->result().base_model_version();
        submission.update.sample_count = request->result().sample_count();
        submission.update.algorithm = algorithm_from_wire(request->result().algorithm());
        submission.update.worker_id = request->worker_id();
        submission.update.nonce = request->result().nonce();
        submission.update.update_id = request->task_id();
        // Tensor payloads (delta / control_delta / refreshed client
        // control variate) are carried as TensorManifest + a
        // chunked/streamed or object-storage-referenced transport per
        // Work Package C; wiring that decoding is the remaining step to
        // make this fully data-complete once a real ModelManifest flows
        // through CreateRun (see config_from_request's note above).
        std::string reason;
        const auto accepted = run.submit_client_result(
            request->worker_id(), request->task_id(), request->lease_id(),
            std::move(submission), now_unix_s(), reason
        );
        response->set_accepted(accepted);
        response->set_reason(reason);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::StreamRunEvents(
    grpc::ServerContext* context,
    const fl::coordinator::v1::StreamRunEventsRequest* request,
    grpc::ServerWriter<fl::events::v1::CoordinatorEvent>* writer
) {
    try {
        std::string cursor = request->resume_after_event_id();
        // Simple poll loop: the domain-layer EventBus (cpp_coordinator/
        // event_bus.hpp) is itself pull-based by design (see its header
        // comment), so a real streaming handler is exactly this —
        // poll, write what's new, sleep, repeat until the client
        // disconnects (context->IsCancelled()).
        while (!context->IsCancelled()) {
            for (const auto& event : manager_->event_bus().poll(request->run_id(), cursor)) {
                fl::events::v1::CoordinatorEvent wire_event;
                wire_event.set_event_id(event.event_id);
                wire_event.set_run_id(event.run_id);
                wire_event.set_round_id(event.round_id);
                wire_event.set_event_type(fl::coordinator::to_string(event.type));
                wire_event.set_timestamp(event.timestamp);
                wire_event.set_trace_id(event.trace_id);
                wire_event.set_client_id(event.client_id);
                wire_event.set_worker_id(event.worker_id);
                wire_event.set_model_version(event.model_version);
                if (!writer->Write(wire_event)) {
                    return grpc::Status::OK;  // client disconnected
                }
                cursor = event.event_id;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::Health(
    grpc::ServerContext*,
    const fl::coordinator::v1::HealthRequest*,
    fl::coordinator::v1::HealthResponse* response
) {
    response->set_status("ok");
    response->set_version("milestone-3");
    const auto uptime = std::chrono::duration_cast<std::chrono::duration<double>>(
        std::chrono::steady_clock::now() - started_at_
    );
    response->set_uptime_seconds(uptime.count());
    response->set_active_runs(static_cast<std::uint32_t>(manager_->list_run_ids().size()));
    response->set_registered_workers(static_cast<std::uint32_t>(manager_->worker_registry().registered_count()));
    return grpc::Status::OK;
}

}  // namespace fl::coordinator
