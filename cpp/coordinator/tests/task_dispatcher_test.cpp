#include "fl_coordinator/task_dispatcher.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

namespace {

fl::core::TensorDescriptor descriptor() {
    return fl::core::TensorDescriptor{.name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32};
}

fl::coordinator::ClientResultSubmission make_submission(const std::string& client_id, double value) {
    fl::coordinator::ClientResultSubmission submission;
    submission.update.run_id = "run-1";
    submission.update.round_id = 1;
    submission.update.client_id = client_id;
    submission.update.update_id = "update-" + client_id;
    submission.update.nonce = "nonce-" + client_id;
    submission.update.base_model_version = "v0";
    submission.update.sample_count = 1;
    submission.update.delta.insert(fl::core::TensorBuffer(descriptor(), {value}));
    return submission;
}

std::vector<fl::coordinator::ClientTaskDescriptor> two_client_descriptors() {
    fl::coordinator::ClientTaskDescriptor a;
    a.run_id = "run-1";
    a.round_id = 1;
    a.client_id = "c1";
    fl::coordinator::ClientTaskDescriptor b = a;
    b.client_id = "c2";
    return {a, b};
}

}  // namespace

void run_task_dispatcher_tests() {
    using fl::coordinator::TaskDispatcher;

    {
        TaskDispatcher dispatcher(/*lease_seconds=*/60, /*max_retries=*/3);
        dispatcher.enqueue(two_client_descriptors());
        check(dispatcher.pending_count() == 2, "enqueue adds exactly the requested tasks");

        const auto first = dispatcher.acquire("worker-1", 0.0);
        check(first.has_value(), "acquire returns a task when one is pending");
        check(dispatcher.pending_count() == 1, "acquiring a task removes it from the pending queue");

        const auto second_attempt = dispatcher.acquire("worker-1", 0.0);
        check(!second_attempt.has_value(), "a worker already holding a lease cannot acquire a second task");

        const auto other_worker = dispatcher.acquire("worker-2", 0.0);
        check(other_worker.has_value(), "a different worker can acquire the remaining task");
    }

    {
        TaskDispatcher dispatcher(60, 3);
        dispatcher.enqueue({two_client_descriptors()[0]});
        const auto task = dispatcher.acquire("worker-1", 0.0).value();

        std::string reason;
        const auto accepted = dispatcher.submit_result(
            "worker-1", task.task_id, task.lease_id, make_submission("c1", 1.0), 1.0, reason
        );
        check(accepted, "a valid, in-lease submission is accepted");
        check(dispatcher.completed_count() == 1, "completed_count reflects the accepted result");

        std::string duplicate_reason;
        const auto duplicate = dispatcher.submit_result(
            "worker-1", task.task_id, task.lease_id, make_submission("c1", 2.0), 2.0, duplicate_reason
        );
        check(!duplicate, "a second submission for an already-completed task is rejected");
        check(!duplicate_reason.empty(), "rejection includes a reason");
    }

    {
        // Late result: lease has already expired by the time of submission.
        TaskDispatcher dispatcher(/*lease_seconds=*/10, 3);
        dispatcher.enqueue({two_client_descriptors()[0]});
        const auto task = dispatcher.acquire("worker-1", 0.0).value();

        std::string reason;
        const auto accepted = dispatcher.submit_result(
            "worker-1", task.task_id, task.lease_id, make_submission("c1", 1.0), /*now=*/11.0, reason
        );
        check(!accepted, "a submission after lease expiry is rejected as a late result");
    }

    {
        // Lease-mismatch: someone submits with a stale/wrong lease_id.
        TaskDispatcher dispatcher(60, 3);
        dispatcher.enqueue({two_client_descriptors()[0]});
        const auto task = dispatcher.acquire("worker-1", 0.0).value();

        std::string reason;
        const auto accepted = dispatcher.submit_result(
            "worker-1", task.task_id, "wrong-lease-id", make_submission("c1", 1.0), 1.0, reason
        );
        check(!accepted, "a submission with a mismatched lease_id is rejected");
    }

    {
        // Expired-lease sweep requeues under max_retries, then permanently
        // fails once retries are exhausted.
        TaskDispatcher dispatcher(/*lease_seconds=*/5, /*max_retries=*/2);
        dispatcher.enqueue({two_client_descriptors()[0]});

        const auto first_lease = dispatcher.acquire("worker-1", 0.0).value();
        check(first_lease.attempt == 1, "first acquisition is attempt 1");
        auto failed = dispatcher.sweep_expired_leases(6.0);  // expired: attempt 1 < max_retries(2) -> requeued
        check(failed.empty(), "a first-time lease expiry is requeued, not permanently failed");
        check(dispatcher.pending_count() == 1, "expired task returns to the pending queue");

        const auto second_lease = dispatcher.acquire("worker-2", 6.0).value();
        check(second_lease.attempt == 2, "requeued task is redispatched as attempt 2");
        failed = dispatcher.sweep_expired_leases(12.0);  // attempt 2 >= max_retries(2) -> permanently failed
        check(failed.size() == 1 && failed[0] == "c1", "exhausting max_retries permanently fails the task");
        check(dispatcher.failed_count() == 1, "failed_count reflects the permanent failure");
        check(dispatcher.all_tasks_settled(), "a permanently failed task counts as settled");
    }
}

}  // namespace fl::coordinator::testing
