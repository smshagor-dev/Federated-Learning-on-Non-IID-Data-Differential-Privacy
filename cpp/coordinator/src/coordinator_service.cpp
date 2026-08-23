// Distributed partition parity wrapper.
//
// The prior coordinator service implementation is preserved byte-for-byte in
// coordinator_service_legacy.cpp.  Only CreateRun is wrapped here so canonical
// dataset-partition metadata can enter RunConfig before the run is created.

#include "fl_coordinator/coordinator_service.hpp"

#define CreateRun CreateRun_legacy
#include "coordinator_service_legacy.cpp"
#undef CreateRun

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
