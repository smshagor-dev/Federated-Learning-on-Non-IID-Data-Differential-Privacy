#pragma once

// Additive compatibility wrapper for distributed partition parity and v3 live
// secure-aggregation recovery.
//
// coordinator_service_legacy.hpp preserves the long-lived service header
// byte-for-byte. The implementation wrapper in coordinator_service.cpp renames
// only the prior CreateRun definition to CreateRun_legacy before including the
// preserved implementation, then supplies the partition-aware CreateRun.
//
// v3 recovery follows the same compatibility discipline: a private recovery
// service member is injected at the existing private helper declaration rather
// than rewriting the large legacy class. The recovery-aware ServerBuilder below
// registers that member beside CoordinatorService when it is configured. The
// ordinary builder API is otherwise inherited unchanged.

#include "fl_coordinator/secure_aggregation_recovery_service_compile.hpp"

namespace grpc {
class RecoveryAwareServerBuilder;
}

#define regenerate_trusted_key_bundle(...)                                           \
    regenerate_trusted_key_bundle(__VA_ARGS__);                                     \
    grpc::Status CreateRun_legacy(                                                   \
        grpc::ServerContext* context,                                                \
        const fl::coordinator::v1::CreateRunRequest* request,                        \
        fl::coordinator::v1::CreateRunResponse* response);                           \
    friend class ::grpc::RecoveryAwareServerBuilder;                                \
    std::unique_ptr<SecureAggregationRecoveryServiceImpl> recovery_service_

#include "fl_coordinator/coordinator_service_legacy.hpp"

#undef regenerate_trusted_key_bundle

namespace grpc {

class RecoveryAwareServerBuilder : public ServerBuilder {
  public:
    using ServerBuilder::RegisterService;

    void RegisterService(fl::coordinator::CoordinatorServiceImpl* service) {
        ServerBuilder::RegisterService(
            static_cast<fl::coordinator::v1::CoordinatorService::Service*>(service));
        if (service != nullptr && service->recovery_service_ != nullptr) {
            ServerBuilder::RegisterService(service->recovery_service_.get());
        }
    }
};

}  // namespace grpc

// coordinator/main.cpp already uses `grpc::ServerBuilder builder;` and then a
// single RegisterService(&service) call. Substituting only that type token keeps
// the entry point byte-for-byte unchanged while making registration additive.
// coordinator_service.cpp immediately undefines this macro after including the
// wrapper so implementation internals are not macro-affected.
#define ServerBuilder RecoveryAwareServerBuilder
