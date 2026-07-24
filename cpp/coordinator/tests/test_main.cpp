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
    guarded("[4/6] scaffold_client_state", [&]() { fl::coordinator::testing::run_scaffold_client_state_tests(scratch_dir + "/scaffold"); });
    guarded("[5/6] run_manager", fl::coordinator::testing::run_run_manager_tests);
    guarded("[6/6] recovery", [&]() { fl::coordinator::testing::run_recovery_tests(scratch_dir + "/recovery"); });

    return fl::coordinator::testing::g_failures == 0 ? 0 : 1;
}
