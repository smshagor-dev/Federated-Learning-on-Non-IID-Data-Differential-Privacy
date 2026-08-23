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
    core/src/privacy.cpp
    core/src/secure_random.cpp
)

target_include_directories(fl_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/core/include
)

if(WIN32)
    # secure_random.cpp's OsEntropySecureRandomProvider uses
    # BCryptGenRandom (bcrypt.lib) on Windows — see
    # docs/cryptographic-primitives.md and
    # docs/secure-aggregation-architecture.md's closure-gate section.
    target_link_libraries(fl_core PRIVATE bcrypt)
endif()

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

add_executable(fl_privacy_tests
    core/tests/privacy_test.cpp
)

target_link_libraries(fl_privacy_tests PRIVATE fl_core)

add_test(NAME fl_privacy_tests COMMAND $<TARGET_FILE:fl_privacy_tests>)

add_executable(fl_secure_random_tests
    core/tests/secure_random_test.cpp
)

target_link_libraries(fl_secure_random_tests PRIVATE fl_core)

add_test(NAME fl_secure_random_tests COMMAND $<TARGET_FILE:fl_secure_random_tests>)

add_executable(fl_aggregate_cli
    core/tools/aggregate_cli.cpp
)

target_link_libraries(fl_aggregate_cli PRIVATE fl_core)

add_executable(fl_aggregation_benchmark
    benchmarks/aggregation_benchmark.cpp
)

target_link_libraries(fl_aggregation_benchmark PRIVATE fl_core)

# ---------------------------------------------------------------------
# Coordinator Runtime phase: coordinator domain layer. Transport-agnostic on purpose —
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
    coordinator/src/worker_identity_registry.cpp
    coordinator/src/replay_protection_store.cpp
    coordinator/src/accountant_monotonicity_store.cpp
    coordinator/src/signing_key_registry.cpp
    coordinator/src/coordinator_signing_key_registry.cpp
    coordinator/src/coordinator_task_sequence_store.cpp
    coordinator/src/idempotency_store.cpp
    coordinator/src/trusted_key_bundle.cpp
    coordinator/src/security_event.cpp
    coordinator/src/security_journal_json.cpp
    coordinator/src/security_event_journal.cpp
    coordinator/src/security_audit_journal.cpp
    coordinator/src/secure_aggregation_encoding.cpp
    coordinator/src/secure_aggregation_mask.cpp
    coordinator/src/secure_aggregation_session.cpp
    coordinator/src/secure_aggregation_session_store.cpp
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
    coordinator/tests/aggregation_manifest_test.cpp
    coordinator/tests/personalization_summary_test.cpp
    coordinator/tests/user_level_dp_test.cpp
    coordinator/tests/adaptive_clipping_test.cpp
    coordinator/tests/hybrid_dp_test.cpp
    coordinator/tests/privacy_budget_policy_test.cpp
    coordinator/tests/privacy_recovery_test.cpp
    coordinator/tests/worker_privacy_capability_test.cpp
    coordinator/tests/worker_identity_registry_test.cpp
    coordinator/tests/replay_protection_store_test.cpp
    coordinator/tests/accountant_monotonicity_store_test.cpp
    coordinator/tests/signing_key_registry_test.cpp
    coordinator/tests/coordinator_signing_key_registry_test.cpp
    coordinator/tests/coordinator_task_sequence_store_test.cpp
    coordinator/tests/idempotency_store_test.cpp
    coordinator/tests/trusted_key_bundle_test.cpp
    coordinator/tests/security_event_test.cpp
    coordinator/tests/security_event_journal_test.cpp
    coordinator/tests/security_audit_journal_test.cpp
    coordinator/tests/fixed_point_encoding_test.cpp
    coordinator/tests/pairwise_mask_test.cpp
    coordinator/tests/cohort_state_machine_test.cpp
    coordinator/tests/secure_aggregation_session_store_test.cpp
    coordinator/tests/round_fault_tolerance_test.cpp
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

    # gRPC::grpc++ already links against OpenSSL transitively (its own
    # TLS implementation needs it), so requiring it explicitly here for
    # Ed25519 signature verification (capability_statement_verifier.cpp,
    # Work Package F) introduces no new dependency for this gRPC-gated
    # build -- see that file's header comment for why this stays out of
    # the non-gRPC-gated fl_coordinator library used by local Windows
    # development.
    find_package(OpenSSL REQUIRED)

    set(GENERATED_PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/generated)

    add_executable(fl_coordinator_grpc_server
        coordinator/src/coordinator_service.cpp
        coordinator/src/transport_credentials.cpp
        coordinator/src/peer_identity.cpp
        coordinator/src/capability_statement_verifier.cpp
        coordinator/src/signed_envelope_verifier.cpp
        coordinator/src/coordinator_signing_identity.cpp
        coordinator/src/coordinator_task_signing.cpp
        coordinator/src/secure_aggregation_crypto.cpp
        coordinator/src/secure_aggregation_tensor_mask.cpp
        coordinator/src/secure_aggregation_session_manager.cpp
        coordinator/main.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.grpc.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )

    target_include_directories(fl_coordinator_grpc_server PRIVATE ${GENERATED_PROTO_DIR})
    target_link_libraries(fl_coordinator_grpc_server
        PRIVATE
            fl_coordinator
            gRPC::grpc++
            protobuf::libprotobuf
            OpenSSL::Crypto
    )

    # Security Administration slice, Work Package G
    # (docs/coordinator-key-recovery.md): a command-line recovery/
    # administration tool operating directly on the coordinator's
    # persisted files -- protobuf-free itself, but still needs real
    # Ed25519 crypto (coordinator_signing_identity.cpp), so it is gated
    # alongside fl_coordinator_grpc_server rather than built locally
    # (this machine has no standalone OpenSSL discoverable outside this
    # gRPC-gated branch either -- confirmed, not assumed).
    add_executable(fl_coordinator_key_admin_cli
        coordinator/tools/coordinator_key_admin_cli.cpp
        coordinator/src/coordinator_signing_identity.cpp
        coordinator/src/signed_envelope_verifier.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )
    target_include_directories(fl_coordinator_key_admin_cli PRIVATE ${GENERATED_PROTO_DIR})
    target_link_libraries(fl_coordinator_key_admin_cli
        PRIVATE
            fl_coordinator
            protobuf::libprotobuf
            OpenSSL::Crypto
    )

    # Privacy Engineering phase: regression coverage for the CreateRun
    # wire-mapping gap (see docs/create-run-wire-mapping.md). Only
    # buildable alongside fl_coordinator_grpc_server itself, for the same
    # reason (needs real generated gRPC/protobuf C++ bindings).
    add_executable(fl_coordinator_grpc_tests
        coordinator/tests/coordinator_service_test.cpp
        coordinator/src/coordinator_service.cpp
        coordinator/src/peer_identity.cpp
        coordinator/src/capability_statement_verifier.cpp
        coordinator/src/signed_envelope_verifier.cpp
        coordinator/src/coordinator_signing_identity.cpp
        coordinator/src/coordinator_task_signing.cpp
        coordinator/src/secure_aggregation_crypto.cpp
        coordinator/src/secure_aggregation_tensor_mask.cpp
        coordinator/src/secure_aggregation_session_manager.cpp
        coordinator/src/transport_credentials.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.grpc.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )
    target_include_directories(fl_coordinator_grpc_tests PRIVATE ${GENERATED_PROTO_DIR})
    target_link_libraries(fl_coordinator_grpc_tests
        PRIVATE
            fl_coordinator
            gRPC::grpc++
            protobuf::libprotobuf
            OpenSSL::Crypto
    )
    add_test(
        NAME fl_coordinator_grpc_tests
        COMMAND $<TARGET_FILE:fl_coordinator_grpc_tests>
    )

    # Standalone unit coverage for canonical JSON encoding + Ed25519
    # verification against a real Python-produced golden vector (see
    # the test file itself) -- gated here for the same OpenSSL/generated-
    # proto reasons as fl_peer_identity_tests below.
    add_executable(fl_capability_statement_verifier_tests
        coordinator/tests/capability_statement_verifier_test.cpp
        coordinator/src/capability_statement_verifier.cpp
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )
    target_include_directories(fl_capability_statement_verifier_tests
        PRIVATE ${GENERATED_PROTO_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_capability_statement_verifier_tests
        PRIVATE
            protobuf::libprotobuf
            OpenSSL::Crypto
    )
    add_test(
        NAME fl_capability_statement_verifier_tests
        COMMAND $<TARGET_FILE:fl_capability_statement_verifier_tests>
    )

    # Standalone unit coverage for SignedWorkerEnvelope canonical JSON,
    # domain-separated signing bytes, and Ed25519 verification -- see
    # the test file itself.
    add_executable(fl_signed_envelope_verifier_tests
        coordinator/tests/signed_envelope_verifier_test.cpp
        coordinator/src/signed_envelope_verifier.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )
    target_include_directories(fl_signed_envelope_verifier_tests
        PRIVATE ${GENERATED_PROTO_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_signed_envelope_verifier_tests
        PRIVATE
            protobuf::libprotobuf
            OpenSSL::Crypto
    )
    add_test(
        NAME fl_signed_envelope_verifier_tests
        COMMAND $<TARGET_FILE:fl_signed_envelope_verifier_tests>
    )

    # Coordinator-Signed Tasks slice: standalone unit coverage for the
    # coordinator's own Ed25519 signing identity, the five configuration
    # hashes, the task payload hash, and end-to-end sign/verify -- see
    # the test file itself.
    add_executable(fl_coordinator_task_signing_tests
        coordinator/tests/coordinator_task_signing_test.cpp
        coordinator/src/coordinator_signing_identity.cpp
        coordinator/src/coordinator_task_signing.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )
    target_include_directories(fl_coordinator_task_signing_tests
        PRIVATE ${GENERATED_PROTO_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_coordinator_task_signing_tests
        PRIVATE
            protobuf::libprotobuf
            OpenSSL::Crypto
    )
    add_test(
        NAME fl_coordinator_task_signing_tests
        COMMAND $<TARGET_FILE:fl_coordinator_task_signing_tests>
    )

    # peer_identity.cpp itself includes <grpcpp/security/auth_context.h>
    # (it reads real AuthContext state), so its unit test is gated
    # alongside the other gRPC-only targets even though the test cases
    # below only exercise the pure has_service_identity/has_worker_identity
    # string-matching logic, not a live handshake — see
    # coordinator/tests/peer_identity_test.cpp for why extract_peer_identity
    # itself is validated live instead of with a fake AuthContext.
    add_executable(fl_peer_identity_tests
        coordinator/tests/peer_identity_test.cpp
        coordinator/src/peer_identity.cpp
    )
    target_include_directories(fl_peer_identity_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_peer_identity_tests PRIVATE gRPC::grpc++ OpenSSL::Crypto)
    add_test(
        NAME fl_peer_identity_tests
        COMMAND $<TARGET_FILE:fl_peer_identity_tests>
    )

    # Secure Aggregation Protocol Foundation and No-Dropout Masked-Sum
    # Core slice: standalone unit coverage for the OpenSSL-backed
    # crypto primitive wrappers (X25519, HKDF-SHA-256, ChaCha20 IETF,
    # SHA-256, cohort commitment, session configuration hash) -- gated
    # here for the same OpenSSL reasons as fl_peer_identity_tests
    # above. Deliberately protobuf-free and does not link fl_coordinator
    # (see secure_aggregation_crypto.hpp's header comment); only needs
    # the coordinator include path for secure_aggregation_session.hpp's
    # struct definition.
    add_executable(fl_secure_aggregation_crypto_tests
        coordinator/tests/secure_aggregation_crypto_test.cpp
        coordinator/src/secure_aggregation_crypto.cpp
        coordinator/src/secure_aggregation_session.cpp
    )
    target_include_directories(fl_secure_aggregation_crypto_tests
        PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_secure_aggregation_crypto_tests PRIVATE OpenSSL::Crypto)
    add_test(
        NAME fl_secure_aggregation_crypto_tests
        COMMAND $<TARGET_FILE:fl_secure_aggregation_crypto_tests>
    )

    # Tensor/weight mask generation (Work Packages T, U) plus the
    # capstone full-cohort-cancellation / dropout-breaks-cancellation
    # integration proof -- needs secure_aggregation_crypto.cpp, so
    # gated here for the same reason as fl_secure_aggregation_crypto_tests.
    add_executable(fl_secure_aggregation_tensor_mask_tests
        coordinator/tests/secure_aggregation_tensor_mask_test.cpp
        coordinator/src/secure_aggregation_crypto.cpp
        coordinator/src/secure_aggregation_session.cpp
        coordinator/src/secure_aggregation_mask.cpp
        coordinator/src/secure_aggregation_encoding.cpp
        coordinator/src/secure_aggregation_tensor_mask.cpp
    )
    target_include_directories(fl_secure_aggregation_tensor_mask_tests
        PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_secure_aggregation_tensor_mask_tests PRIVATE OpenSSL::Crypto)
    add_test(
        NAME fl_secure_aggregation_tensor_mask_tests
        COMMAND $<TARGET_FILE:fl_secure_aggregation_tensor_mask_tests>
    )

    # Secure Aggregation Wire Protocol and Live No-Dropout Execution
    # slice, Work Package D: the coordinator session-manager
    # orchestration class. Needs the real generated protobuf message
    # types (coordinator.pb.cc/worker.pb.cc, plus their own imports) --
    # gRPC service stubs (coordinator.grpc.pb.cc) are not needed since
    # this manager is not wired into CoordinatorServiceImpl this pass
    # (see coordinator_service.cpp's six UNIMPLEMENTED overrides).
    # Links fl_core directly (TensorCollection/TensorBuffer, needed by
    # finalize()'s decoded-aggregate bridge) rather than duplicating its
    # source list.
    add_executable(fl_secure_aggregation_session_manager_tests
        coordinator/tests/secure_aggregation_session_manager_test.cpp
        coordinator/src/secure_aggregation_session_manager.cpp
        coordinator/src/secure_aggregation_session_store.cpp
        coordinator/src/coordinator_signing_identity.cpp
        coordinator/src/secure_aggregation_crypto.cpp
        coordinator/src/secure_aggregation_session.cpp
        coordinator/src/secure_aggregation_mask.cpp
        coordinator/src/secure_aggregation_encoding.cpp
        coordinator/src/secure_aggregation_tensor_mask.cpp
        ${GENERATED_PROTO_DIR}/coordinator/coordinator.pb.cc
        ${GENERATED_PROTO_DIR}/worker/worker.pb.cc
        ${GENERATED_PROTO_DIR}/experiment/experiment.pb.cc
        ${GENERATED_PROTO_DIR}/events/events.pb.cc
        ${GENERATED_PROTO_DIR}/common/artifact.pb.cc
        ${GENERATED_PROTO_DIR}/privacy/privacy.pb.cc
    )
    target_include_directories(fl_secure_aggregation_session_manager_tests
        PRIVATE ${GENERATED_PROTO_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/coordinator/include)
    target_link_libraries(fl_secure_aggregation_session_manager_tests
        PRIVATE
            fl_core
            protobuf::libprotobuf
            OpenSSL::Crypto
    )
    add_test(
        NAME fl_secure_aggregation_session_manager_tests
        COMMAND $<TARGET_FILE:fl_secure_aggregation_session_manager_tests>
    )
else()
    message(STATUS "gRPC not found; skipping fl_coordinator_grpc_server (see docs/coordinator-runtime.md). "
                    "Use fl_coordinator_cli for local development/testing instead.")
endif()
