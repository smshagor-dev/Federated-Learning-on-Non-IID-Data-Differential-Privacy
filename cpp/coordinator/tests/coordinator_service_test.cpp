// Additive distributed-partition regression coverage.
// Preserve the existing gRPC service test byte-for-byte and wrap only its
// top-level runner, matching coordinator_service.cpp/run_manager.cpp's legacy
// wrapper convention.

#define run_coordinator_service_tests run_coordinator_service_tests_legacy
#include "coordinator_service_test_legacy.cpp"
#undef run_coordinator_service_tests

#include <string>

namespace fl::coordinator::testing {

void run_coordinator_service_tests() {
    run_coordinator_service_tests_legacy();

    std::filesystem::remove_all("coordinator_partition_test_scratch");
    CoordinatorConfig coordinator_config;
    RunManager manager(coordinator_config,
                       "coordinator_partition_test_scratch/checkpoints",
                       "coordinator_partition_test_scratch/scaffold");
    CoordinatorServiceImpl service(manager);

    // Full advanced-partition mapping: CreateRun accepts the structured
    // DatasetConfig and AcquireTask emits the versioned reference consumed by
    // the Python worker. dataset_reference is subsequently covered by the
    // existing dataset_partition_hash/task signature pipeline.
    {
        auto request = make_wire_request("run-pathological-partition");
        request.set_client_selection_seed(42);
        auto* dataset = request.mutable_config()->mutable_dataset();
        dataset->set_name("CIFAR100");
        dataset->set_partitioning("pathological");
        dataset->set_classes_per_client(3);
        dataset->set_min_client_size(11);

        fl::coordinator::v1::CreateRunResponse create_response;
        const auto create_status = service.CreateRun(nullptr, &request, &create_response);
        check(create_status.ok(),
              "CreateRun accepts pathological partition metadata: " +
                  create_status.error_message());

        fl::worker::v1::RegisterWorkerRequest register_request;
        register_request.set_worker_id("partition-worker");
        fl::worker::v1::RegisterWorkerResponse register_response;
        check(service.RegisterWorker(nullptr, &register_request, &register_response).ok(),
              "partition worker registration succeeds");

        fl::coordinator::v1::StartRunRequest start_request;
        start_request.set_run_id("run-pathological-partition");
        fl::coordinator::v1::RunStateResponse start_response;
        check(service.StartRun(nullptr, &start_request, &start_response).ok(),
              "partition run starts");

        fl::coordinator::v1::AcquireTaskRequest acquire_request;
        acquire_request.set_worker_id("partition-worker");
        acquire_request.set_run_id("run-pathological-partition");
        fl::coordinator::v1::ClientTrainingTask task_response;
        check(service.AcquireTask(nullptr, &acquire_request, &task_response).ok(),
              "partition task acquisition succeeds");
        check(task_response.task_available(), "partition task is available");
        const std::string reference = task_response.task().dataset_reference();
        check(reference.rfind("fl-partition-v1://synthetic?", 0) == 0,
              "task carries versioned canonical partition reference");
        check(reference.find("dataset=CIFAR100") != std::string::npos,
              "partition reference carries dataset identity");
        check(reference.find("strategy=pathological") != std::string::npos,
              "partition reference carries pathological strategy");
        check(reference.find("classes_per_client=3") != std::string::npos,
              "partition reference carries classes_per_client");
        check(reference.find("min_client_size=11") != std::string::npos,
              "partition reference carries minimum client size");
        check(reference.find("seed=42") != std::string::npos,
              "partition reference carries canonical execution seed");
    }

    // Fail closed at run creation, not later on a worker, when an advanced
    // partition strategy is structurally invalid.
    {
        auto request = make_wire_request("run-invalid-dirichlet");
        auto* dataset = request.mutable_config()->mutable_dataset();
        dataset->set_name("CIFAR10");
        dataset->set_partitioning("dirichlet");
        dataset->set_alpha(0.0);

        fl::coordinator::v1::CreateRunResponse response;
        const auto status = service.CreateRun(nullptr, &request, &response);
        check(!status.ok(), "CreateRun rejects dirichlet alpha <= 0");
        check(status.error_message().find("alpha > 0") != std::string::npos,
              "invalid dirichlet rejection is explicit");
    }

    // Quantity-skew parameters survive the same C++ mapping contract.
    {
        auto request = make_wire_request("run-quantity-skew");
        auto* dataset = request.mutable_config()->mutable_dataset();
        dataset->set_name("MNIST");
        dataset->set_partitioning("quantity_skew");
        dataset->set_quantity_skew_sigma(1.25);
        dataset->set_min_client_size(9);

        fl::coordinator::v1::CreateRunResponse response;
        check(service.CreateRun(nullptr, &request, &response).ok(),
              "CreateRun accepts quantity-skew metadata");
    }
}

}  // namespace fl::coordinator::testing
