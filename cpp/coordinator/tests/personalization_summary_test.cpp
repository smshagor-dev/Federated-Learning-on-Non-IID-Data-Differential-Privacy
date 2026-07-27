#include "fl_coordinator/run_manager.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

namespace {

fl::core::ModelManifest make_manifest() {
    return fl::core::ModelManifest{
        .model_id = "toy",
        .model_version = "v0",
        .tensors = {fl::core::TensorDescriptor{
            .name = "weight", .shape = {2}, .dtype = fl::core::DType::kFloat32}},
    };
}

fl::coordinator::RunConfig make_config(const std::string& run_id) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kDitto;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.target_clients_per_round = 1;
    config.total_clients = 1;
    config.max_rounds = 1;
    config.minimum_valid_results = 1;
    config.client_ids = {"client-a"};
    return config;
}

fl::coordinator::ClientResultSubmission make_result_with_personalization(
    const fl::coordinator::DispatchedTask& task, bool include_personalization) {
    fl::coordinator::ClientResultSubmission submission;
    submission.update.run_id = task.descriptor.run_id;
    submission.update.round_id = task.descriptor.round_id;
    submission.update.client_id = task.descriptor.client_id;
    submission.update.update_id = "update-" + task.descriptor.client_id;
    submission.update.nonce = "nonce-" + task.descriptor.client_id;
    submission.update.base_model_version = task.descriptor.model_version;
    submission.update.algorithm = task.descriptor.algorithm;
    submission.update.sample_count = 10;
    submission.update.delta.insert(fl::core::TensorBuffer(
        fl::core::TensorDescriptor{
            .name = "weight", .shape = {2}, .dtype = fl::core::DType::kFloat32},
        {1.0, 2.0}));
    if (include_personalization) {
        PersonalizationMetricRecord record;
        record.client_id = task.descriptor.client_id;
        record.round_id = task.descriptor.round_id;
        record.algorithm = "ditto";
        record.global_local_accuracy = 0.6;
        record.personalized_local_accuracy = 0.75;
        record.global_local_loss = 1.1;
        record.personalized_local_loss = 0.8;
        record.sample_count = 10;
        record.personalized_improvement = 0.15;
        record.personalized_model_version = 1;
        record.recorded_at = "1000";
        record.has_personalized_model = true;
        submission.personalization_metrics = record;
    }
    return submission;
}

}  // namespace

void run_personalization_summary_tests() {
    // --- Submitting personalization metrics makes them retrievable ---
    {
        CoordinatorConfig coordinator_config;
        RunManager manager(coordinator_config,
                           "personalization_scratch/checkpoints_a",
                           "personalization_scratch/scaffold_a");
        manager.create_run(make_config("run-a"), 0.0);
        auto& run = manager.get("run-a");
        manager.worker_registry().register_worker(
            "worker-a", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("trace", 0.0);
        run.advance(1.0);
        const auto task = run.acquire_task("worker-a", 1.0).value();

        std::string reason;
        const auto accepted = run.submit_client_result("worker-a",
                                                       task.task_id,
                                                       task.lease_id,
                                                       make_result_with_personalization(task, true),
                                                       2.0,
                                                       reason);
        check(accepted, "submission with personalization metrics is accepted: " + reason);

        const auto summary = run.personalization_summary();
        check(summary.size() == 1,
              "personalization_summary has exactly one record after one submission");
        if (summary.size() == 1) {
            check(summary[0].client_id == "client-a", "record client_id matches");
            check(summary[0].personalized_local_accuracy == 0.75,
                  "record personalized accuracy matches");
            check(summary[0].personalized_improvement == 0.15, "record improvement matches");
        }
    }

    // --- Submitting without personalization metrics leaves the summary empty ---
    {
        CoordinatorConfig coordinator_config;
        RunManager manager(coordinator_config,
                           "personalization_scratch/checkpoints_b",
                           "personalization_scratch/scaffold_b");
        manager.create_run(make_config("run-b"), 0.0);
        auto& run = manager.get("run-b");
        manager.worker_registry().register_worker(
            "worker-a", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("trace", 0.0);
        run.advance(1.0);
        const auto task = run.acquire_task("worker-a", 1.0).value();

        std::string reason;
        run.submit_client_result("worker-a",
                                 task.task_id,
                                 task.lease_id,
                                 make_result_with_personalization(task, false),
                                 2.0,
                                 reason);
        check(run.personalization_summary().empty(),
              "no personalization_metrics submitted: summary stays empty");
    }

    // --- Survives a checkpoint/restore across a fresh RunInstance
    // (the CLI bridge's actual process-per-call model) ---
    {
        CoordinatorConfig coordinator_config;
        const std::string checkpoint_dir = "personalization_scratch/checkpoints_c";
        const std::string scaffold_dir = "personalization_scratch/scaffold_c";
        {
            RunManager manager(coordinator_config, checkpoint_dir, scaffold_dir);
            manager.create_run(make_config("run-c"), 0.0);
            auto& run = manager.get("run-c");
            manager.worker_registry().register_worker(
                "worker-a", fl::coordinator::WorkerCapability{}, 0.0);
            run.start("trace", 0.0);
            run.advance(1.0);
            const auto task = run.acquire_task("worker-a", 1.0).value();
            std::string reason;
            run.submit_client_result("worker-a",
                                     task.task_id,
                                     task.lease_id,
                                     make_result_with_personalization(task, true),
                                     2.0,
                                     reason);
        }
        // Fresh RunManager/RunInstance, exactly like a new CLI-bridge invocation.
        {
            RunManager manager(coordinator_config, checkpoint_dir, scaffold_dir);
            manager.create_run(make_config("run-c"), 3.0);
            auto& run = manager.get("run-c");
            run.restore_from_checkpoint();
            const auto summary = run.personalization_summary();
            check(summary.size() == 1,
                  "personalization_summary survives checkpoint/restore across a fresh RunInstance");
            if (summary.size() == 1) {
                check(summary[0].personalized_local_accuracy == 0.75,
                      "restored record's accuracy matches");
            }
        }
    }
}

}  // namespace fl::coordinator::testing
