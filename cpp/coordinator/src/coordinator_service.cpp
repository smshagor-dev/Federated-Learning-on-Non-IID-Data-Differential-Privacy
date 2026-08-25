// Distributed partition parity + v3 live recovery compatibility wrapper.
//
// The prior coordinator service implementation is preserved byte-for-byte in
// coordinator_service_legacy.cpp. CreateRun is wrapped for canonical dataset
// partition metadata. Recovery is injected additively through the constructor's
// existing secure-aggregation dependency tail; no recovery service is created
// for legacy/unit-test instances that did not provide all security/session
// authorities. Recovery is also never exposed on insecure-development or
// server-TLS-only listeners: raw Shamir shares are secret protocol material, so
// this RPC surface requires the coordinator's mTLS-required transport mode.

#include "fl_coordinator/coordinator_service.hpp"

// coordinator_service.hpp deliberately leaves this type-token substitution
// active for coordinator/main.cpp so its existing grpc::ServerBuilder becomes
// recovery-aware. This implementation file must not carry the macro into the
// preserved legacy implementation.
#undef ServerBuilder

#define CreateRun CreateRun_legacy
#define secure_aggregation_masked_update_window_seconds_(...)                       \
    secure_aggregation_masked_update_window_seconds_(__VA_ARGS__),                  \
        recovery_service_(                                                          \
            transport_mode == TransportMode::kMtlsRequired &&                       \
                    identity_registry != nullptr && signing_key_registry != nullptr && \
                    replay_store != nullptr && secure_aggregation_manager != nullptr \
                ? std::make_unique<SecureAggregationRecoveryServiceImpl>(           \
                      manager,                                                       \
                      *identity_registry,                                            \
                      *signing_key_registry,                                         \
                      *replay_store,                                                 \
                      *secure_aggregation_manager)                                   \
                : nullptr)
#include "coordinator_service_legacy.cpp"
#undef secure_aggregation_masked_update_window_seconds_
#undef CreateRun

// The main CMake target already compiles coordinator_service.cpp in both the
// live server and gRPC test binary. Including the generated recovery service
// implementation here keeps this additive slice out of the long explicit
// source lists while still producing exactly one copy per binary. Generation is
// handled by scripts/generate_protos.sh before the gRPC build.
#include "recovery/recovery.pb.cc"
#include "recovery/recovery.grpc.pb.cc"

#include <cmath>
#include <stdexcept>
#include <string>

namespace fl::coordinator {
namespace {

void apply_dataset_partition_config(const fl::coordinator::v1::CreateRunRequest& request,
                                    RunConfig& config) {
    const auto& dataset = request.config().dataset();

    // Backward compatibility for direct/legacy tests and callers that predate
    // DatasetConfig. Canonical Go executions always send both values.
    config.dataset_name = dataset.name().empty() ? "synthetic" : dataset.name();
    config.dataset_partitioning = dataset.partitioning().empty() ? "iid" : dataset.partitioning();
    config.dataset_alpha = dataset.alpha();
    config.dataset_classes_per_client = dataset.classes_per_client();
    config.dataset_quantity_skew_sigma = dataset.quantity_skew_sigma();
    config.dataset_min_client_size = dataset.min_client_size();

    const auto finite = [](double value) { return std::isfinite(value); };
    if (config.dataset_partitioning == "iid") {
        return;
    }
    if (config.dataset_partitioning == "dirichlet") {
        if (!finite(config.dataset_alpha) || config.dataset_alpha <= 0.0) {
            throw std::invalid_argument("dirichlet partition requires alpha > 0");
        }
        return;
    }
    if (config.dataset_partitioning == "pathological") {
        if (config.dataset_classes_per_client == 0) {
            throw std::invalid_argument(
                "pathological partition requires classes_per_client > 0");
        }
        return;
    }
    if (config.dataset_partitioning == "quantity_skew") {
        if (!finite(config.dataset_quantity_skew_sigma) ||
            config.dataset_quantity_skew_sigma <= 0.0) {
            throw std::invalid_argument(
                "quantity_skew partition requires quantity_skew_sigma > 0");
        }
        return;
    }
    throw std::invalid_argument("unsupported dataset partitioning: " +
                                config.dataset_partitioning);
}

}  // namespace

grpc::Status CoordinatorServiceImpl::CreateRun(
    grpc::ServerContext*,
    const fl::coordinator::v1::CreateRunRequest* request,
    fl::coordinator::v1::CreateRunResponse* response) {
    try {
        auto config = config_from_request(*request);
        apply_dataset_partition_config(*request, config);
        const auto run_id = manager_->create_run(std::move(config), now_unix_s());
        response->set_run_id(run_id);
        response->set_state(fl::core::to_string(manager_->get(run_id).snapshot().state));
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

}  // namespace fl::coordinator
