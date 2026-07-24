// Real gRPC coordinator server entry point. Only built when gRPC is
// found (see cpp/CMakeLists.txt's fl_coordinator_grpc_server target) —
// see docs/coordinator-runtime.md for why that's CI-only in this
// environment, and cpp/coordinator/tools/coordinator_cli.cpp for the
// local-development substitute that actually runs here.
#include "fl_coordinator/coordinator_service.hpp"

#include <cstdlib>
#include <iostream>
#include <string>

#include <grpcpp/grpcpp.h>

int main(int argc, char** argv) {
    std::string bind_address = "0.0.0.0:50051";
    if (argc > 1) {
        bind_address = argv[1];
    }

    fl::coordinator::CoordinatorConfig config;
    fl::coordinator::RunManager manager(config, "checkpoints", "scaffold_state");
    fl::coordinator::CoordinatorServiceImpl service(manager);

    grpc::ServerBuilder builder;
    // Insecure credentials are appropriate only for local development;
    // see docs/coordinator-runtime.md's security section for the TLS/mTLS
    // configuration hook this leaves for a production deployment.
    builder.AddListeningPort(bind_address, grpc::InsecureServerCredentials());
    builder.RegisterService(&service);
    builder.SetMaxReceiveMessageSize(64 * 1024 * 1024);
    builder.SetMaxSendMessageSize(64 * 1024 * 1024);

    const auto server = builder.BuildAndStart();
    if (!server) {
        std::cerr << "failed to start coordinator gRPC server on " << bind_address << "\n";
        return 1;
    }
    // std::cout is fully buffered when stdout isn't a TTY (true for
    // `docker logs` and CI) — without an explicit flush, this line (and
    // therefore the container's only startup confirmation) never actually
    // reaches the log until the buffer fills or the process exits, making
    // a healthy server look silent/hung. See structured_log.hpp's comment
    // for why per-event logging uses std::cerr instead.
    std::cout << "fl coordinator gRPC server listening on " << bind_address << std::endl;
    server->Wait();
    return 0;
}
