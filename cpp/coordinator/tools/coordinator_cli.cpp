// Local cross-language compatibility bridge for the C++ coordinator,
// extending the same pattern as cpp/core/tools/aggregate_cli.cpp: one
// process per call, driven over stdin/stdout with a plain-text
// "key=value" protocol, so Python and Go clients can exercise the real,
// fully-tested coordinator domain layer (fl_coordinator) without needing
// a local gRPC C++ build (see docs/coordinator-runtime.md for why that's
// CI-only in this environment).
//
// Continuity across calls comes from the coordinator's own checkpoint/
// recovery machinery (Work Package G), not from a long-lived process:
// every invocation loads state from --state-dir (creating it fresh if
// this is the first call for the run), performs exactly one operation,
// persists state back, and exits. A real gRPC server would instead hold
// a RunManager in memory for its whole lifetime; this bridge trades that
// efficiency for being runnable and testable today.
#include "fl_coordinator/run_manager.hpp"

#include <filesystem>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {

using fl::coordinator::ClientResultSubmission;
using fl::coordinator::ClientTaskDescriptor;
using fl::coordinator::CoordinatorConfig;
using fl::coordinator::DispatchedTask;
using fl::coordinator::RunConfig;
using fl::coordinator::RunManager;
using fl::coordinator::WorkerCapability;

std::vector<std::string> split(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::size_t start = 0;
    while (true) {
        const auto position = value.find(delimiter, start);
        if (position == std::string::npos) {
            parts.push_back(value.substr(start));
            break;
        }
        parts.push_back(value.substr(start, position - start));
        start = position + 1;
    }
    return parts;
}

std::map<std::string, std::string> read_request(std::istream& in) {
    std::map<std::string, std::string> fields;
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.empty()) {
            continue;
        }
        const auto position = line.find('=');
        if (position == std::string::npos) {
            throw std::invalid_argument("invalid request line: " + line);
        }
        fields[line.substr(0, position)] = line.substr(position + 1);
    }
    return fields;
}

std::string field(const std::map<std::string, std::string>& fields, const std::string& key, const std::string& fallback = "") {
    const auto it = fields.find(key);
    return it == fields.end() ? fallback : it->second;
}

double now_from_field(const std::map<std::string, std::string>& fields) {
    const auto value = field(fields, "now", "0");
    return std::stod(value);
}

fl::core::TensorDescriptor make_weight_descriptor(std::uint64_t elements) {
    return fl::core::TensorDescriptor{.name = "weight", .shape = {elements}, .dtype = fl::core::DType::kFloat32};
}

// This bridge assumes a single-tensor "weight" manifest, matching the
// synthetic integration tests it's built for (see
// docs/coordinator-runtime.md's stated scope). A production gRPC service
// carries the full ModelManifest over the wire instead of this
// hard-coded shape.
RunConfig parse_run_config(const std::map<std::string, std::string>& fields) {
    RunConfig config;
    config.run_id = field(fields, "run_id");
    config.manifest.model_id = field(fields, "model_id", "toy");
    config.manifest.model_version = "v0";
    config.manifest.tensors = {make_weight_descriptor(std::stoull(field(fields, "tensor_elements", "1")))};

    const auto algorithm = field(fields, "algorithm", "fedavg");
    if (algorithm == "fedavg") config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    else if (algorithm == "fedprox") config.algorithm = fl::core::AggregationAlgorithm::kFedProx;
    else if (algorithm == "scaffold") config.algorithm = fl::core::AggregationAlgorithm::kScaffold;
    else if (algorithm == "fedadagrad") config.algorithm = fl::core::AggregationAlgorithm::kFedAdagrad;
    else if (algorithm == "fedadam") config.algorithm = fl::core::AggregationAlgorithm::kFedAdam;
    else if (algorithm == "fedyogi") config.algorithm = fl::core::AggregationAlgorithm::kFedYogi;
    else throw std::invalid_argument("unknown algorithm: " + algorithm);

    const auto weighting = field(fields, "weighting", "uniform");
    if (weighting == "uniform") config.weighting = fl::core::WeightingStrategyType::kUniform;
    else if (weighting == "sample_count") config.weighting = fl::core::WeightingStrategyType::kSampleCount;
    else config.weighting = fl::core::WeightingStrategyType::kUniform;

    config.server_lr = std::stod(field(fields, "server_lr", "1.0"));
    config.beta1 = std::stod(field(fields, "beta1", "0.9"));
    config.beta2 = std::stod(field(fields, "beta2", "0.99"));
    config.tau = std::stod(field(fields, "tau", "1e-3"));
    config.contribution_cap = std::stod(field(fields, "contribution_cap", "1.0"));
    config.target_clients_per_round = static_cast<std::uint32_t>(std::stoul(field(fields, "target_clients_per_round", "1")));
    config.total_clients = static_cast<std::uint32_t>(std::stoul(field(fields, "total_clients", "1")));
    config.max_rounds = static_cast<std::uint32_t>(std::stoul(field(fields, "max_rounds", "1")));
    config.round_timeout_seconds = static_cast<std::uint32_t>(std::stoul(field(fields, "round_timeout_seconds", "300")));
    config.minimum_valid_results = static_cast<std::uint32_t>(std::stoul(field(fields, "minimum_valid_results", "1")));
    config.client_selection_seed = std::stoull(field(fields, "seed", "0"));
    config.task_lease_seconds = static_cast<std::uint32_t>(std::stoul(field(fields, "task_lease_seconds", "60")));
    config.max_task_retries = static_cast<std::uint32_t>(std::stoul(field(fields, "max_task_retries", "3")));
    config.local_epochs = static_cast<std::uint32_t>(std::stoul(field(fields, "local_epochs", "1")));
    config.batch_size = static_cast<std::uint32_t>(std::stoul(field(fields, "batch_size", "32")));
    config.learning_rate = std::stod(field(fields, "learning_rate", "0.01"));
    config.momentum = std::stod(field(fields, "momentum", "0.0"));
    config.weight_decay = std::stod(field(fields, "weight_decay", "0.0"));
    config.fedprox_mu = std::stod(field(fields, "fedprox_mu", "0.0"));

    const auto client_ids_raw = field(fields, "client_ids");
    if (!client_ids_raw.empty()) {
        config.client_ids = split(client_ids_raw, ',');
    }
    return config;
}

void print_run_snapshot(const fl::coordinator::RunSnapshot& snapshot) {
    std::cout << "run_id=" << snapshot.run_id << "\n";
    std::cout << "state=" << fl::core::to_string(snapshot.state) << "\n";
    std::cout << "current_round=" << snapshot.current_round << "\n";
    std::cout << "max_rounds=" << snapshot.max_rounds << "\n";
    std::cout << "model_version=" << snapshot.model_version << "\n";
    std::cout << "algorithm=" << fl::core::to_string(snapshot.algorithm) << "\n";
    std::cout << "registered_workers=" << snapshot.registered_workers << "\n";
    std::cout << "healthy_workers=" << snapshot.healthy_workers << "\n";
}

std::string encode_tensor(const std::string& name, const fl::core::TensorBuffer& tensor) {
    std::ostringstream out;
    out << name << "|f32|";
    const auto& shape = tensor.descriptor().shape;
    for (std::size_t index = 0; index < shape.size(); ++index) {
        if (index > 0) out << "-";
        out << shape[index];
    }
    out << "|";
    const auto& values = tensor.values();
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) out << ",";
        out << std::setprecision(17) << values[index];
    }
    return out.str();
}

fl::core::TensorBuffer decode_tensor(const std::string& field_value) {
    const auto parts = split(field_value, '|');
    if (parts.size() != 4) {
        throw std::invalid_argument("malformed tensor field: " + field_value);
    }
    fl::core::TensorDescriptor descriptor;
    descriptor.name = parts[0];
    descriptor.dtype = fl::core::DType::kFloat32;
    if (!parts[2].empty()) {
        for (const auto& dim : split(parts[2], '-')) descriptor.shape.push_back(std::stoull(dim));
    }
    std::vector<double> values;
    if (!parts[3].empty()) {
        for (const auto& raw : split(parts[3], ',')) values.push_back(std::stod(raw));
    }
    return fl::core::TensorBuffer(std::move(descriptor), std::move(values));
}

void print_task(const DispatchedTask& task) {
    std::cout << "has_task=1\n";
    std::cout << "task_id=" << task.task_id << "\n";
    std::cout << "lease_id=" << task.lease_id << "\n";
    std::cout << "client_id=" << task.descriptor.client_id << "\n";
    std::cout << "round_id=" << task.descriptor.round_id << "\n";
    std::cout << "model_version=" << task.descriptor.model_version << "\n";
    std::cout << "algorithm=" << fl::core::to_string(task.descriptor.algorithm) << "\n";
    std::cout << "local_epochs=" << task.descriptor.local_epochs << "\n";
    std::cout << "batch_size=" << task.descriptor.batch_size << "\n";
    std::cout << "learning_rate=" << task.descriptor.learning_rate << "\n";
    std::cout << "fedprox_mu=" << task.descriptor.fedprox_mu << "\n";
}

// Loads (or freshly creates, on first use for this run_id) a RunManager
// rooted at the given state directory and returns it alongside the
// existing-or-not flag, so callers can decide whether to call
// create_run() (first call) or restore_from_checkpoint() (every call
// after).
RunManager make_manager(const std::string& state_dir) {
    CoordinatorConfig coordinator_config;
    return RunManager(coordinator_config, state_dir + "/checkpoints", state_dir + "/scaffold");
}

bool checkpoint_exists(const std::string& state_dir, const std::string& run_id) {
    return std::filesystem::exists(state_dir + "/checkpoints/" + run_id + ".checkpoint");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage: fl_coordinator_cli <command> <state_dir>\n";
        return 2;
    }
    const std::string command = argv[1];
    const std::string state_dir = argv[2];

    try {
        const auto fields = read_request(std::cin);
        const auto run_id = field(fields, "run_id");
        auto manager = make_manager(state_dir);

        if (command == "create-run") {
            auto config = parse_run_config(fields);
            manager.create_run(config, now_from_field(fields));
            std::cout << "status=ok\n";
            print_run_snapshot(manager.get(run_id).snapshot());
            return 0;
        }

        // Every other command operates on a run that may have prior
        // checkpointed state; reconstruct it from the same config (the
        // caller is expected to pass the same run parameters every time,
        // mirroring the documented recovery contract in run_manager.hpp)
        // and restore on top of it if a checkpoint exists.
        auto config = parse_run_config(fields);
        manager.create_run(config, now_from_field(fields));
        auto& run = manager.get(run_id);
        if (checkpoint_exists(state_dir, run_id)) {
            run.restore_from_checkpoint();
        }

        const auto now = now_from_field(fields);
        const auto trace_id = field(fields, "trace_id");

        // WorkerRegistry is intentionally not part of the checkpointed
        // state (only round/model/optimizer state is durable — see
        // run_manager.hpp), so every fresh CLI invocation starts with an
        // empty registry. Since register_worker() is an idempotent
        // refresh for an already-known worker_id, auto-registering here
        // on any call that names a worker is a safe, simple substitute
        // for persisting registry state across calls in this per-call
        // process model.
        const auto worker_id = field(fields, "worker_id");
        if (!worker_id.empty() && command != "register-worker") {
            WorkerCapability capability;
            capability.device = field(fields, "device", "cpu");
            manager.worker_registry().register_worker(worker_id, capability, now);
        }

        if (command == "start-run") {
            run.start(trace_id, now);
        } else if (command == "pause-run") {
            run.pause(field(fields, "reason"), trace_id, now);
        } else if (command == "resume-run") {
            run.resume(trace_id, now);
        } else if (command == "cancel-run") {
            run.cancel(field(fields, "reason"), trace_id, now);
        } else if (command == "get-run") {
            // no-op: snapshot printed below regardless of command
        } else if (command == "advance") {
            run.advance(now);
        } else if (command == "register-worker") {
            WorkerCapability capability;
            capability.device = field(fields, "device", "cpu");
            manager.worker_registry().register_worker(field(fields, "worker_id"), capability, now);
        } else if (command == "acquire-task") {
            run.advance(now);  // ensure a round has been dispatched if one is due
            const auto task = run.acquire_task(field(fields, "worker_id"), now);
            std::cout << "status=ok\n";
            if (task.has_value()) {
                print_task(*task);
                if (config.algorithm == fl::core::AggregationAlgorithm::kScaffold) {
                    const auto [global_cv, client_cv] = run.scaffold_control_variates_for(task->descriptor.client_id);
                    for (const auto& [name, tensor] : global_cv.tensors()) {
                        std::cout << "global_control_variate=" << encode_tensor(name, tensor) << "\n";
                    }
                    for (const auto& [name, tensor] : client_cv.tensors()) {
                        std::cout << "client_control_variate=" << encode_tensor(name, tensor) << "\n";
                    }
                }
            } else {
                std::cout << "has_task=0\n";
            }
            print_run_snapshot(run.snapshot());
            return 0;
        } else if (command == "submit-result") {
            ClientResultSubmission submission;
            submission.update.run_id = run_id;
            submission.update.round_id = std::stoull(field(fields, "round_id"));
            submission.update.client_id = field(fields, "client_id");
            submission.update.update_id = field(fields, "update_id");
            submission.update.nonce = field(fields, "nonce");
            submission.update.worker_id = field(fields, "worker_id");
            submission.update.base_model_version = field(fields, "base_model_version");
            submission.update.algorithm = config.algorithm;
            submission.update.sample_count = std::stoull(field(fields, "sample_count"));
            submission.update.delta.insert(decode_tensor(field(fields, "delta")));
            if (fields.contains("control_delta")) {
                submission.update.control_delta.insert(decode_tensor(field(fields, "control_delta")));
            }
            if (fields.contains("refreshed_client_control_variate")) {
                submission.refreshed_client_control_variate.insert(decode_tensor(field(fields, "refreshed_client_control_variate")));
            }
            std::string reason;
            const auto accepted = run.submit_client_result(
                field(fields, "worker_id"), field(fields, "task_id"), field(fields, "lease_id"),
                std::move(submission), now, reason
            );
            run.advance(now);  // opportunistically finalize the round if this was the last result needed
            std::cout << "status=ok\n";
            std::cout << "accepted=" << (accepted ? 1 : 0) << "\n";
            std::cout << "reason=" << reason << "\n";
            print_run_snapshot(run.snapshot());
            return 0;
        } else {
            std::cerr << "unknown command: " << command << "\n";
            return 2;
        }

        std::cout << "status=ok\n";
        print_run_snapshot(run.snapshot());
        return 0;
    } catch (const std::exception& error) {
        std::cout << "status=error\n";
        std::cout << "message=" << error.what() << "\n";
        return 1;
    }
}
