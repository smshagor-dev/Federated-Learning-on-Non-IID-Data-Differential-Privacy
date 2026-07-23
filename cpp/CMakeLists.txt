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
