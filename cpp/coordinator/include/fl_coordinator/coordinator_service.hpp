#pragma once

// Additive compatibility wrapper for distributed partition parity.
//
// coordinator_service_legacy.hpp preserves the long-lived service header
// byte-for-byte. The implementation wrapper in coordinator_service.cpp renames
// only the prior CreateRun definition to CreateRun_legacy before including the
// preserved implementation, then supplies the partition-aware CreateRun. To
// keep every translation unit on the exact same class definition without
// rewriting the large header, inject that one internal declaration at the
// existing private helper declaration below. This mirrors the repository's
// run_manager legacy-wrapper pattern while leaving all public RPC declarations
// untouched.
#define regenerate_trusted_key_bundle(...)                                           \
    regenerate_trusted_key_bundle(__VA_ARGS__);                                     \
    grpc::Status CreateRun_legacy(                                                   \
        grpc::ServerContext* context,                                                \
        const fl::coordinator::v1::CreateRunRequest* request,                        \
        fl::coordinator::v1::CreateRunResponse* response)

#include "fl_coordinator/coordinator_service_legacy.hpp"

#undef regenerate_trusted_key_bundle
