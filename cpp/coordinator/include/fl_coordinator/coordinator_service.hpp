#pragma once

// Real gRPC service adapter over fl_coordinator's RunManager/RunInstance
// domain layer. This header (and its .cpp) are only compiled when gRPC
// is actually found (see cpp/CMakeLists.txt's optional
// fl_coordinator_grpc_server target, gated on find_package(gRPC)) — this
// machine has no local C++ gRPC toolchain (see docs/coordinator-runtime.md
// for the full explanation), so this code has been written to the same
// standard as the rest of fl_coordinator but has only been build-checked
// in CI (ubuntu-latest, where `apt-get install libgrpc++-dev
// protobuf-compiler-grpc` provides everything needed), not locally.
//
// The class deliberately does nothing but translate between protobuf
// messages and RunManager/RunInstance calls — all actual domain logic
// (validation, idempotency, checkpointing, event ordering) lives in
// fl_coordinator and is unit-tested there (cpp/coordinator/tests/),
// independent of whether gRPC is available.

#include "coordinator/coordinator.grpc.pb.h"
#include "fl_coordinator/run_manager.hpp"

#include <chrono>

namespace fl::coordinator {

class CoordinatorServiceImpl final : public fl::coordinator::v1::CoordinatorService::Service {
public:
    explicit CoordinatorServiceImpl(RunManager& manager);

    grpc::Status CreateRun(
        grpc::ServerContext* context,
        const fl::coordinator::v1::CreateRunRequest* request,
        fl::coordinator::v1::CreateRunResponse* response
    ) override;

    grpc::Status StartRun(
        grpc::ServerContext* context,
        const fl::coordinator::v1::StartRunRequest* request,
        fl::coordinator::v1::RunStateResponse* response
    ) override;

    grpc::Status PauseRun(
        grpc::ServerContext* context,
        const fl::coordinator::v1::PauseRunRequest* request,
        fl::coordinator::v1::RunStateResponse* response
    ) override;

    grpc::Status ResumeRun(
        grpc::ServerContext* context,
        const fl::coordinator::v1::ResumeRunRequest* request,
        fl::coordinator::v1::RunStateResponse* response
    ) override;

    grpc::Status CancelRun(
        grpc::ServerContext* context,
        const fl::coordinator::v1::CancelRunRequest* request,
        fl::coordinator::v1::RunStateResponse* response
    ) override;

    grpc::Status GetRun(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetRunRequest* request,
        fl::coordinator::v1::RunDetails* response
    ) override;

    grpc::Status RegisterWorker(
        grpc::ServerContext* context,
        const fl::worker::v1::RegisterWorkerRequest* request,
        fl::worker::v1::RegisterWorkerResponse* response
    ) override;

    grpc::Status Heartbeat(
        grpc::ServerContext* context,
        const fl::worker::v1::WorkerHeartbeatRequest* request,
        fl::worker::v1::WorkerHeartbeatResponse* response
    ) override;

    grpc::Status AcquireTask(
        grpc::ServerContext* context,
        const fl::coordinator::v1::AcquireTaskRequest* request,
        fl::coordinator::v1::ClientTrainingTask* response
    ) override;

    grpc::Status SubmitClientResult(
        grpc::ServerContext* context,
        const fl::coordinator::v1::SubmitClientResultRequest* request,
        fl::coordinator::v1::SubmitClientResultResponse* response
    ) override;

    grpc::Status StreamRunEvents(
        grpc::ServerContext* context,
        const fl::coordinator::v1::StreamRunEventsRequest* request,
        grpc::ServerWriter<fl::events::v1::CoordinatorEvent>* writer
    ) override;

    grpc::Status Health(
        grpc::ServerContext* context,
        const fl::coordinator::v1::HealthRequest* request,
        fl::coordinator::v1::HealthResponse* response
    ) override;

private:
    RunManager* manager_;
    std::chrono::steady_clock::time_point started_at_;

    [[nodiscard]] static double now_unix_s();
};

}  // namespace fl::coordinator
