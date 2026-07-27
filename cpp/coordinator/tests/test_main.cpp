#include "test_support.hpp"

#include <functional>
#include <iostream>
#include <string>

namespace fl::coordinator::testing {
void run_worker_registry_tests();
void run_task_dispatcher_tests();
void run_event_bus_tests();
void run_scaffold_client_state_tests(const std::string& scratch_dir);
void run_run_manager_tests();
void run_recovery_tests(const std::string& scratch_dir);
void run_aggregation_manifest_tests();
void run_personalization_summary_tests();
void run_user_level_dp_tests();
void run_adaptive_clipping_tests();
void run_hybrid_dp_tests();
void run_privacy_budget_policy_tests();
void run_privacy_recovery_tests(const std::string& scratch_dir);
void run_worker_privacy_capability_tests();
void run_worker_identity_registry_tests(const std::string& scratch_dir);
void run_replay_protection_store_tests(const std::string& scratch_dir);
void run_accountant_monotonicity_store_tests(const std::string& scratch_dir);
void run_signing_key_registry_tests(const std::string& scratch_dir);
void run_coordinator_signing_key_registry_tests(const std::string& scratch_dir);
void run_coordinator_task_sequence_store_tests(const std::string& scratch_dir);
void run_idempotency_store_tests(const std::string& scratch_dir);
void run_trusted_key_bundle_tests(const std::string& scratch_dir);
void run_security_event_tests();
void run_security_event_journal_tests(const std::string& scratch_dir);
void run_security_audit_journal_tests(const std::string& scratch_dir);
void run_fixed_point_encoding_tests();
void run_pairwise_mask_tests();
void run_cohort_state_machine_tests();
void run_secure_aggregation_session_store_tests(const std::string& scratch_dir);
}  // namespace fl::coordinator::testing

namespace {
void guarded(const std::string& label, const std::function<void()>& fn) {
    std::cout << label << "..." << std::flush;
    try {
        fn();
        std::cout << " done\n" << std::flush;
    } catch (const std::exception& error) {
        std::cout << " EXCEPTION: " << error.what() << "\n" << std::flush;
        ++fl::coordinator::testing::g_failures;
    } catch (...) {
        std::cout << " UNKNOWN EXCEPTION\n" << std::flush;
        ++fl::coordinator::testing::g_failures;
    }
}
}  // namespace

int main(int argc, char** argv) {
    const std::string scratch_dir = argc > 1 ? argv[1] : "coordinator_test_scratch";

    guarded("[1/6] worker_registry", fl::coordinator::testing::run_worker_registry_tests);
    guarded("[2/6] task_dispatcher", fl::coordinator::testing::run_task_dispatcher_tests);
    guarded("[3/6] event_bus", fl::coordinator::testing::run_event_bus_tests);
    guarded("[4/6] scaffold_client_state", [&]() {
        fl::coordinator::testing::run_scaffold_client_state_tests(scratch_dir + "/scaffold");
    });
    guarded("[5/8] run_manager", fl::coordinator::testing::run_run_manager_tests);
    guarded("[6/8] recovery",
            [&]() { fl::coordinator::testing::run_recovery_tests(scratch_dir + "/recovery"); });
    guarded("[7/14] aggregation_manifest", fl::coordinator::testing::run_aggregation_manifest_tests);
    guarded("[8/14] personalization_summary",
            fl::coordinator::testing::run_personalization_summary_tests);
    guarded("[9/14] user_level_dp", fl::coordinator::testing::run_user_level_dp_tests);
    guarded("[10/14] adaptive_clipping", fl::coordinator::testing::run_adaptive_clipping_tests);
    guarded("[11/14] hybrid_dp", fl::coordinator::testing::run_hybrid_dp_tests);
    guarded("[12/14] privacy_budget_policy",
            fl::coordinator::testing::run_privacy_budget_policy_tests);
    guarded("[13/14] privacy_recovery", [&]() {
        fl::coordinator::testing::run_privacy_recovery_tests(scratch_dir + "/privacy_recovery");
    });
    guarded("[14/16] worker_privacy_capability",
            fl::coordinator::testing::run_worker_privacy_capability_tests);
    guarded("[15/16] worker_identity_registry", [&]() {
        fl::coordinator::testing::run_worker_identity_registry_tests(scratch_dir +
                                                                      "/worker_identity_registry");
    });
    guarded("[16/17] replay_protection_store", [&]() {
        fl::coordinator::testing::run_replay_protection_store_tests(scratch_dir +
                                                                     "/replay_protection_store");
    });
    guarded("[17/18] accountant_monotonicity_store", [&]() {
        fl::coordinator::testing::run_accountant_monotonicity_store_tests(
            scratch_dir + "/accountant_monotonicity_store");
    });
    guarded("[18/22] signing_key_registry", [&]() {
        fl::coordinator::testing::run_signing_key_registry_tests(scratch_dir +
                                                                  "/signing_key_registry");
    });
    guarded("[19/22] coordinator_signing_key_registry", [&]() {
        fl::coordinator::testing::run_coordinator_signing_key_registry_tests(
            scratch_dir + "/coordinator_signing_key_registry");
    });
    guarded("[20/22] coordinator_task_sequence_store", [&]() {
        fl::coordinator::testing::run_coordinator_task_sequence_store_tests(
            scratch_dir + "/coordinator_task_sequence_store");
    });
    guarded("[21/22] idempotency_store", [&]() {
        fl::coordinator::testing::run_idempotency_store_tests(scratch_dir + "/idempotency_store");
    });
    guarded("[22/25] trusted_key_bundle", [&]() {
        fl::coordinator::testing::run_trusted_key_bundle_tests(scratch_dir + "/trusted_key_bundle");
    });
    guarded("[23/25] security_event", fl::coordinator::testing::run_security_event_tests);
    guarded("[24/25] security_event_journal", [&]() {
        fl::coordinator::testing::run_security_event_journal_tests(scratch_dir +
                                                                     "/security_event_journal");
    });
    guarded("[25/28] security_audit_journal", [&]() {
        fl::coordinator::testing::run_security_audit_journal_tests(scratch_dir +
                                                                     "/security_audit_journal");
    });
    guarded("[26/29] fixed_point_encoding", fl::coordinator::testing::run_fixed_point_encoding_tests);
    guarded("[27/29] pairwise_mask", fl::coordinator::testing::run_pairwise_mask_tests);
    guarded("[28/29] cohort_state_machine", fl::coordinator::testing::run_cohort_state_machine_tests);
    guarded("[29/29] secure_aggregation_session_store", [&]() {
        fl::coordinator::testing::run_secure_aggregation_session_store_tests(
            scratch_dir + "/secure_aggregation_session_store");
    });

    return fl::coordinator::testing::g_failures == 0 ? 0 : 1;
}
