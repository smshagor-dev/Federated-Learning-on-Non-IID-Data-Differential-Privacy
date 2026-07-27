#include "fl_coordinator/run_manager.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

namespace {

fl::core::ModelManifest make_manifest_with(std::vector<fl::core::TensorDescriptor> tensors) {
    return fl::core::ModelManifest{
        .model_id = "toy", .model_version = "v0", .tensors = std::move(tensors)};
}

fl::coordinator::RunConfig make_config(const std::string& run_id,
                                       fl::core::AggregationAlgorithm algorithm,
                                       fl::coordinator::AggregationManifest aggregation_manifest,
                                       std::vector<fl::core::TensorDescriptor> tensors) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest_with(std::move(tensors));
    config.aggregation_manifest = std::move(aggregation_manifest);
    config.algorithm = algorithm;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.target_clients_per_round = 1;
    config.total_clients = 1;
    config.max_rounds = 1;
    config.minimum_valid_results = 1;
    config.client_ids = {"client-a"};
    return config;
}

fl::core::TensorDescriptor tensor(const std::string& name, std::uint64_t elements = 1) {
    return fl::core::TensorDescriptor{
        .name = name, .shape = {elements}, .dtype = fl::core::DType::kFloat32};
}

fl::coordinator::ClientResultSubmission make_result_with_tensors(
    const fl::coordinator::DispatchedTask& task, const std::vector<std::string>& tensor_names) {
    fl::coordinator::ClientResultSubmission submission;
    submission.update.run_id = task.descriptor.run_id;
    submission.update.round_id = task.descriptor.round_id;
    submission.update.client_id = task.descriptor.client_id;
    submission.update.update_id = "update-" + task.descriptor.client_id;
    submission.update.nonce = "nonce-" + task.descriptor.client_id;
    submission.update.base_model_version = task.descriptor.model_version;
    submission.update.algorithm = task.descriptor.algorithm;
    submission.update.sample_count = 4;
    for (const auto& name : tensor_names) {
        submission.update.delta.insert(fl::core::TensorBuffer(tensor(name), {1.0}));
    }
    return submission;
}

}  // namespace

void run_aggregation_manifest_tests() {
    // --- New algorithm enum mappings (Work Package L) ---
    for (const auto algorithm : {
             fl::core::AggregationAlgorithm::kFedSam,
             fl::core::AggregationAlgorithm::kDitto,
             fl::core::AggregationAlgorithm::kPerFedAvg,
         }) {
        CoordinatorConfig coordinator_config;
        RunManager manager(coordinator_config,
                           "agg_manifest_scratch/checkpoints_" + fl::core::to_string(algorithm),
                           "agg_manifest_scratch/scaffold_" + fl::core::to_string(algorithm));
        manager.create_run(
            make_config("run-" + fl::core::to_string(algorithm), algorithm, {}, {tensor("weight")}),
            0.0);
        auto& run = manager.get("run-" + fl::core::to_string(algorithm));
        check(run.snapshot().algorithm == algorithm,
              "new algorithm round-trips through RunConfig: " + fl::core::to_string(algorithm));
    }

    // --- Aggregation manifest: shared tensor accepted, personalized rejected ---
    {
        CoordinatorConfig coordinator_config;
        RunManager manager(coordinator_config,
                           "agg_manifest_scratch/checkpoints_reject",
                           "agg_manifest_scratch/scaffold_reject");
        AggregationManifest aggregation_manifest;
        aggregation_manifest.shared_parameter_names = {"shared"};
        aggregation_manifest.personalized_parameter_names = {"head"};
        manager.create_run(make_config("run-reject",
                                       fl::core::AggregationAlgorithm::kDitto,
                                       aggregation_manifest,
                                       {tensor("shared", 2)}),
                           0.0);
        auto& run = manager.get("run-reject");
        manager.worker_registry().register_worker(
            "worker-a", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("trace", 0.0);
        run.advance(1.0);
        const auto task = run.acquire_task("worker-a", 1.0).value();

        std::string reason;
        const auto rejected_accepted =
            run.submit_client_result("worker-a",
                                     task.task_id,
                                     task.lease_id,
                                     make_result_with_tensors(task, {"shared", "head"}),
                                     2.0,
                                     reason);
        check(!rejected_accepted,
              "submission containing an unauthorized personalized tensor is rejected");
        check(reason.find("head") != std::string::npos,
              "rejection reason names the offending tensor");
        check(reason.find("personalized-only") != std::string::npos,
              "rejection reason explains why");

        const auto accepted = run.submit_client_result("worker-a",
                                                       task.task_id,
                                                       task.lease_id,
                                                       make_result_with_tensors(task, {"shared"}),
                                                       3.0,
                                                       reason);
        check(accepted, "submission containing only shared tensors is accepted: " + reason);
    }

    // --- Frozen tensor also rejected ---
    {
        CoordinatorConfig coordinator_config;
        RunManager manager(coordinator_config,
                           "agg_manifest_scratch/checkpoints_frozen",
                           "agg_manifest_scratch/scaffold_frozen");
        AggregationManifest aggregation_manifest;
        aggregation_manifest.shared_parameter_names = {"shared"};
        aggregation_manifest.frozen_parameter_names = {"frozen_stats"};
        manager.create_run(make_config("run-frozen",
                                       fl::core::AggregationAlgorithm::kFedSam,
                                       aggregation_manifest,
                                       {tensor("shared", 2)}),
                           0.0);
        auto& run = manager.get("run-frozen");
        manager.worker_registry().register_worker(
            "worker-a", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("trace", 0.0);
        run.advance(1.0);
        const auto task = run.acquire_task("worker-a", 1.0).value();

        std::string reason;
        const auto accepted =
            run.submit_client_result("worker-a",
                                     task.task_id,
                                     task.lease_id,
                                     make_result_with_tensors(task, {"shared", "frozen_stats"}),
                                     2.0,
                                     reason);
        check(!accepted, "submission containing a frozen tensor is rejected");
        check(reason.find("frozen") != std::string::npos,
              "rejection reason mentions frozen: " + reason);
    }

    // --- No manifest declared: permissive (backward compatible with FedAvg/FedProx/SCAFFOLD) ---
    {
        CoordinatorConfig coordinator_config;
        RunManager manager(coordinator_config,
                           "agg_manifest_scratch/checkpoints_permissive",
                           "agg_manifest_scratch/scaffold_permissive");
        manager.create_run(
            make_config(
                "run-permissive", fl::core::AggregationAlgorithm::kFedAvg, {}, {tensor("weight")}),
            0.0);
        auto& run = manager.get("run-permissive");
        manager.worker_registry().register_worker(
            "worker-a", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("trace", 0.0);
        run.advance(1.0);
        const auto task = run.acquire_task("worker-a", 1.0).value();

        std::string reason;
        const auto accepted = run.submit_client_result("worker-a",
                                                       task.task_id,
                                                       task.lease_id,
                                                       make_result_with_tensors(task, {"weight"}),
                                                       2.0,
                                                       reason);
        check(accepted, "no aggregation manifest declared: any tensor name is accepted: " + reason);
    }
}

}  // namespace fl::coordinator::testing
