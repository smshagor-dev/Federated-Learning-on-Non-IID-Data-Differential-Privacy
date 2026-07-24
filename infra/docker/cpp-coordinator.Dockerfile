# Real gRPC coordinator server, built for real here. cpp/CMakeLists.txt
# only configures the fl_coordinator_grpc_server target when
# find_package(Protobuf) and find_package(gRPC) both succeed — true on
# this Ubuntu base image via apt, even though it is not true on the
# Windows/MSVC host this repo is otherwise developed on (see
# docs/coordinator-runtime.md and docs/known-limitations.md). This is the
# first environment in which that target has actually been compiled.
FROM mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       protobuf-compiler protobuf-compiler-grpc libprotobuf-dev libgrpc++-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY proto ./proto
COPY scripts/generate_protos.sh ./scripts/generate_protos.sh
COPY cpp ./cpp

# Regenerates cpp/generated/**/*.pb.cc and coordinator.grpc.pb.cc using
# the apt-installed protoc + grpc_cpp_plugin — the same script used for
# local/CI generation (see scripts/generate_protos.sh), so this container
# never silently diverges from how the bindings are produced elsewhere.
RUN bash scripts/generate_protos.sh generated

RUN cmake -S cpp -B build/cpp -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build/cpp --target fl_coordinator_grpc_server -j"$(nproc)"

EXPOSE 50051
ENTRYPOINT ["/app/build/cpp/fl_coordinator_grpc_server"]
CMD ["0.0.0.0:50051"]
