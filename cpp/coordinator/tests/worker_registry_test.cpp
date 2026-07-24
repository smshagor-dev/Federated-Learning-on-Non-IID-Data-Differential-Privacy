#include "fl_coordinator/worker_registry.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

void run_worker_registry_tests() {
    using fl::coordinator::WorkerCapability;
    using fl::coordinator::WorkerRegistry;
    using fl::coordinator::WorkerStatus;

    {
        WorkerRegistry registry(/*missed_heartbeat_threshold=*/3, /*heartbeat_interval_seconds=*/10);
        WorkerCapability capability;
        capability.device = "cpu";
        capability.cpu_count = 4;

        const auto info = registry.register_worker("worker-1", capability, /*now=*/0.0);
        check(info.status == WorkerStatus::kRegistering, "new worker starts REGISTERING");
        check(registry.registered_count() == 1, "registered_count reflects one worker");

        // Idempotent re-registration (e.g. after a network blip) refreshes
        // rather than rejecting.
        const auto refreshed = registry.register_worker("worker-1", capability, /*now=*/5.0);
        check(refreshed.last_heartbeat_unix_s == 5.0, "re-registration refreshes last_heartbeat");
        check(registry.registered_count() == 1, "re-registration does not create a duplicate entry");
    }

    {
        WorkerRegistry registry(3, 10);
        registry.register_worker("worker-1", WorkerCapability{}, 0.0);
        registry.heartbeat("worker-1", WorkerStatus::kIdle, "", 0.0);
        check(registry.healthy_count() == 1, "IDLE worker counts as healthy");

        // threshold(3) * interval(10) = 30s; at t=31 the worker has missed
        // its heartbeat window and must be marked unhealthy.
        const auto newly_unhealthy = registry.sweep_unhealthy(31.0);
        check(newly_unhealthy.size() == 1 && newly_unhealthy[0] == "worker-1", "missed-heartbeat sweep marks worker unhealthy");
        check(registry.get("worker-1")->status == WorkerStatus::kUnhealthy, "worker status updated to UNHEALTHY");
        check(registry.healthy_count() == 0, "unhealthy worker no longer counts as healthy");

        // A second sweep should not re-report an already-unhealthy worker.
        const auto second_sweep = registry.sweep_unhealthy(32.0);
        check(second_sweep.empty(), "sweep only reports newly-unhealthy transitions, not repeats");
    }

    {
        WorkerRegistry registry(3, 10);
        expect_throw(
            [&]() { registry.heartbeat("never-registered", WorkerStatus::kIdle, "", 0.0); },
            "heartbeat from an unregistered worker_id is rejected"
        );
    }

    {
        WorkerRegistry registry(3, 10);
        registry.register_worker("worker-1", WorkerCapability{}, 0.0);
        registry.set_current_task("worker-1", "task-1");
        check(registry.get("worker-1")->status == WorkerStatus::kBusy, "assigning a task marks the worker BUSY");
        registry.clear_current_task("worker-1");
        check(registry.get("worker-1")->status == WorkerStatus::kIdle, "clearing the task returns the worker to IDLE");

        registry.record_failure("worker-1");
        registry.record_failure("worker-1");
        check(registry.get("worker-1")->consecutive_failures == 2, "consecutive_failures accumulates");
        registry.record_success("worker-1");
        check(registry.get("worker-1")->consecutive_failures == 0, "a success resets consecutive_failures");
    }
}

}  // namespace fl::coordinator::testing
