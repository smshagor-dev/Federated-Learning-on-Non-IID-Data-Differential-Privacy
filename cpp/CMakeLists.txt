cmake_minimum_required(VERSION 3.20)
project(fl_super_system_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CTest)

add_library(fl_core
    core/src/build_info.cpp
    core/src/tensor.cpp
    core/src/aggregation.cpp
    core/src/coordinator.cpp
    core/src/checkpoint.cpp
)

target_include_directories(fl_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/core/include
)

add_executable(fl_core_smoke
    core/tests/smoke.cpp
)

target_link_libraries(fl_core_smoke PRIVATE fl_core)

add_test(NAME fl_core_smoke COMMAND $<TARGET_FILE:fl_core_smoke>)

add_executable(fl_aggregator_golden
    core/tests/golden.cpp
)

target_link_libraries(fl_aggregator_golden PRIVATE fl_core)

add_test(NAME fl_aggregator_golden COMMAND $<TARGET_FILE:fl_aggregator_golden>)

add_executable(fl_validation_tests
    core/tests/validation.cpp
)

target_link_libraries(fl_validation_tests PRIVATE fl_core)

add_test(NAME fl_validation_tests COMMAND $<TARGET_FILE:fl_validation_tests>)

add_executable(fl_checkpoint_tests
    core/tests/checkpoint.cpp
)

target_link_libraries(fl_checkpoint_tests PRIVATE fl_core)

add_test(
    NAME fl_checkpoint_tests
    COMMAND $<TARGET_FILE:fl_checkpoint_tests> "$<TARGET_FILE_DIR:fl_checkpoint_tests>/checkpoint_test_scratch"
)

add_executable(fl_aggregate_cli
    core/tools/aggregate_cli.cpp
)

target_link_libraries(fl_aggregate_cli PRIVATE fl_core)

add_executable(fl_aggregation_benchmark
    benchmarks/aggregation_benchmark.cpp
)

target_link_libraries(fl_aggregation_benchmark PRIVATE fl_core)

# ---------------------------------------------------------------------
# Milestone 3: coordinator domain layer. Transport-agnostic on purpose —
# see docs/coordinator-runtime.md for why the real gRPC service adapter
# is a separate, CI-only-built target rather than part of this library.
# ---------------------------------------------------------------------
add_library(fl_coordinator
    coordinator/src/worker_registry.cpp
    coordinator/src/task_dispatcher.cpp
    coordinator/src/event_bus.cpp
    coordinator/src/round_manager.cpp
    coordinator/src/scaffold_client_state.cpp
    coordinator/src/structured_log.cpp
    coordinator/src/run_manager.cpp
)

target_include_directories(fl_coordinator
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include
)

target_link_libraries(fl_coordinator PUBLIC fl_core)

add_executable(fl_coordinator_tests
    coordinator/tests/worker_registry_test.cpp
    coordinator/tests/task_dispatcher_test.cpp
    coordinator/tests/event_bus_test.cpp
    coordinator/tests/scaffold_client_state_test.cpp
    coordinator/tests/run_manager_test.cpp
    coordinator/tests/recovery_test.cpp
    coordinator/tests/test_main.cpp
)

target_link_libraries(fl_coordinator_tests PRIVATE fl_coordinator)

add_test(
    NAME fl_coordinator_tests
    COMMAND $<TARGET_FILE:fl_coordinator_tests> "$<TARGET_FILE_DIR:fl_coordinator_tests>/coordinator_test_scratch"
)

add_executable(fl_coordinator_cli
    coordinator/tools/coordinator_cli.cpp
)

target_link_libraries(fl_coordinator_cli PRIVATE fl_coordinator)

# ---------------------------------------------------------------------
# Real gRPC coordinator server. Only configured when gRPC/Protobuf are
# actually found — not the case on this Windows dev machine (see
# docs/coordinator-runtime.md and docs/known-limitations.md), so this
# target is skipped locally and built/tested in CI instead
# (ubuntu-latest: `apt-get install libgrpc++-dev protobuf-compiler-grpc`).
# ---------------------------------------------------------------------
find_package(Protobuf QUIET)
find_package(gRPC QUIET)

if(Protobuf_FOUND AND gRPC_FOUND)
    message(STATUS "gRPC found: configuring fl_coordinator_grpc_server")

    set(GENERATED_PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/generated)

    add_executable(fl_coordinator_grpc_server
        coordinator/src/coordinator_service.cpp
        coordinator/main.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.grpc.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
    )

    target_include_directories(fl_coordinator_grpc_server PRIVATE ${GENERATED_PROTO_DIR})
    target_link_libraries(fl_coordinator_grpc_server
        PRIVATE
            fl_coordinator
            gRPC::grpc++
            protobuf::libprotobuf
    )
else()
    message(STATUS "gRPC not found; skipping fl_coordinator_grpc_server (see docs/coordinator-runtime.md). "
                    "Use fl_coordinator_cli for local development/testing instead.")
endif()
