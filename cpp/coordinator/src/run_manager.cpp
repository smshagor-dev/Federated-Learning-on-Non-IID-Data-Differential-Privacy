#include "fl_coordinator/run_manager.hpp"

#include "fl_coordinator/structured_log.hpp"

#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <system_error>

namespace fl::coordinator {

namespace {

std::string format_iso8601(double now_unix_s) {
    const auto seconds = static_cast<std::time_t>(now_unix_s);
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &seconds);
#else
    gmtime_r(&seconds, &tm_utc);
#endif
    std::ostringstream out;
    out << std::put_time(&tm_utc, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

fl::core::TensorCollection make_zero_collection(const fl::core::ModelManifest& manifest) {
    fl::core::TensorCollection collection;
    for (const auto& descriptor : manifest.tensors) {
        collection.insert(fl::core::zeros_like(descriptor));
    }
    return collection;
}

std::uint64_t fnv1a_hash(const std::string& data) {
    std::uint64_t hash = 1469598103934665603ULL;
    for (const unsigned char byte : data) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string hash_to_hex(std::uint64_t hash) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

void write_collection(std::ostringstream& out, const std::string& key, const fl::core::TensorCollection& collection) {
    out << key << "_count=" << collection.tensors().size() << "\n";
    for (const auto& [name, tensor] : collection.tensors()) {
        out << key << "_tensor=" << name << "|f32|";
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
        out << "\n";
    }
}

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

std::string encode_tensor_field(const std::string& name, const fl::core::TensorBuffer& tensor) {
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

fl::core::TensorBuffer parse_tensor_field(const std::string& field) {
    // Matches write_collection's "name|dtype|shape|values" format (4
    // segments) — must stay in sync with that function.
    const auto parts = split(field, '|');
    if (parts.size() != 4) {
        throw std::invalid_argument("malformed checkpoint tensor field");
    }
    fl::core::TensorDescriptor descriptor;
    descriptor.name = parts[0];
    descriptor.dtype = fl::core::DType::kFloat32;
    if (!parts[2].empty()) {
        for (const auto& dim : split(parts[2], '-')) {
            descriptor.shape.push_back(std::stoull(dim));
        }
    }
    std::vector<double> values;
    if (!parts[3].empty()) {
        for (const auto& raw_value : split(parts[3], ',')) {
            values.push_back(std::stod(raw_value));
        }
    }
    return fl::core::TensorBuffer(std::move(descriptor), std::move(values));
}

// Semicolon-joined list of encode_tensor_field entries, used to embed a
// whole TensorCollection inside one tab-separated round_result line
// (see save_checkpoint) without needing block markers.
std::string join_tensor_collection(const fl::core::TensorCollection& collection) {
    std::ostringstream out;
    bool first = true;
    for (const auto& [name, tensor] : collection.tensors()) {
        if (!first) out << ";";
        out << encode_tensor_field(name, tensor);
        first = false;
    }
    return out.str();
}

fl::core::TensorCollection parse_tensor_collection_list(const std::string& value) {
    fl::core::TensorCollection collection;
    if (value.empty()) {
        return collection;
    }
    for (const auto& entry : split(value, ';')) {
        collection.insert(parse_tensor_field(entry));
    }
    return collection;
}

std::string encode_round_result(const std::string& client_id, const ClientResultSubmission& submission) {
    const auto& update = submission.update;
    std::ostringstream out;
    out << client_id << "\t" << update.run_id << "\t" << update.round_id << "\t" << update.base_model_version << "\t"
        << fl::core::to_string(update.algorithm) << "\t" << update.sample_count << "\t" << update.nonce << "\t"
        << update.update_id << "\t" << update.worker_id << "\t" << join_tensor_collection(update.delta) << "\t"
        << join_tensor_collection(update.control_delta) << "\t"
        << join_tensor_collection(submission.refreshed_client_control_variate);
    return out.str();
}

fl::core::AggregationAlgorithm algorithm_from_string(const std::string& value) {
    if (value == "fedavg") return fl::core::AggregationAlgorithm::kFedAvg;
    if (value == "fedprox") return fl::core::AggregationAlgorithm::kFedProx;
    if (value == "scaffold") return fl::core::AggregationAlgorithm::kScaffold;
    if (value == "fedadagrad") return fl::core::AggregationAlgorithm::kFedAdagrad;
    if (value == "fedadam") return fl::core::AggregationAlgorithm::kFedAdam;
    if (value == "fedyogi") return fl::core::AggregationAlgorithm::kFedYogi;
    throw std::invalid_argument("unknown algorithm in checkpoint: " + value);
}

std::pair<std::string, ClientResultSubmission> parse_round_result(const std::string& line) {
    const auto parts = split(line, '\t');
    if (parts.size() != 12) {
        throw std::runtime_error("malformed round_result checkpoint line");
    }
    ClientResultSubmission submission;
    submission.update.client_id = parts[0];
    submission.update.run_id = parts[1];
    submission.update.round_id = std::stoull(parts[2]);
    submission.update.base_model_version = parts[3];
    submission.update.algorithm = algorithm_from_string(parts[4]);
    submission.update.sample_count = std::stoull(parts[5]);
    submission.update.nonce = parts[6];
    submission.update.update_id = parts[7];
    submission.update.worker_id = parts[8];
    submission.update.delta = parse_tensor_collection_list(parts[9]);
    submission.update.control_delta = parse_tensor_collection_list(parts[10]);
    submission.refreshed_client_control_variate = parse_tensor_collection_list(parts[11]);
    return {parts[0], submission};
}

}  // namespace

RunManagerError::RunManagerError(const std::string& what) : std::runtime_error(what) {}

// ------------------------------------------------------------------ //
// RunInstance
// ------------------------------------------------------------------ //

RunInstance::RunInstance(
    RunConfig config,
    CoordinatorConfig coordinator_config,
    EventBus& event_bus,
    WorkerRegistry& worker_registry,
    ClientAlgorithmStateStore* scaffold_store,
    std::string checkpoint_directory
)
    : config_(std::move(config)),
      coordinator_config_(coordinator_config),
      event_bus_(&event_bus),
      worker_registry_(&worker_registry),
      scaffold_store_(scaffold_store),
      checkpoint_directory_(std::move(checkpoint_directory)),
      global_model_(make_zero_collection(config_.manifest)) {}

RunSnapshot RunInstance::snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    RunSnapshot snapshot;
    snapshot.run_id = config_.run_id;
    snapshot.state = state_machine_.state();
    snapshot.current_round = current_round_id_;
    snapshot.max_rounds = config_.max_rounds;
    snapshot.model_version = model_version_;
    snapshot.algorithm = config_.algorithm;
    snapshot.registered_workers = worker_registry_->registered_count();
    snapshot.healthy_workers = worker_registry_->healthy_count();
    return snapshot;
}

std::optional<RoundSnapshot> RunInstance::round_snapshot(std::uint64_t round_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (round_id != current_round_id_ || !dispatcher_) {
        return std::nullopt;
    }
    RoundSnapshot snapshot;
    snapshot.run_id = config_.run_id;
    snapshot.round_id = current_round_id_;
    snapshot.state = state_machine_.state();
    snapshot.selected_clients = current_cohort_;
    snapshot.completed_client_ids = dispatcher_->completed_client_ids();
    snapshot.failed_client_ids = dispatcher_->failed_client_ids();
    snapshot.minimum_valid_results = config_.minimum_valid_results;
    return snapshot;
}

void RunInstance::transition(fl::core::RunState next, const std::string& reason, double now_unix_s) {
    state_machine_.transition_to(next, reason, format_iso8601(now_unix_s));
    // Every transition persists a checkpoint, not just round finalization
    // — otherwise a restart between (say) StartRun and the first round
    // completing would forget that the run had been started at all. This
    // is what makes lifecycle actions (start/pause/resume/cancel) durable
    // across a coordinator restart, not just round progress.
    save_checkpoint(now_unix_s);
}

CoordinatorEvent RunInstance::emit(
    CoordinatorEventType type, const std::string& reason, double now_unix_s, std::map<std::string, std::string> metadata
) {
    CoordinatorEvent event;
    event.run_id = config_.run_id;
    event.round_id = current_round_id_;
    event.type = type;
    event.model_version = model_version_;
    event.trace_id = trace_id_;
    event.reason = reason;
    event.metadata = std::move(metadata);
    auto published = event_bus_->publish(std::move(event), format_iso8601(now_unix_s));
    log_event(published);
    return published;
}

void RunInstance::start(const std::string& trace_id, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    trace_id_ = trace_id;
    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kRunning || current == fl::core::RunState::kWaitingForClients ||
        current == fl::core::RunState::kAggregating || current == fl::core::RunState::kEvaluating ||
        current == fl::core::RunState::kCheckpointing) {
        return;  // idempotent: already running, never start a second execution loop
    }
    if (current == fl::core::RunState::kCompleted || current == fl::core::RunState::kFailed ||
        current == fl::core::RunState::kCanceled) {
        throw RunManagerError("cannot start a run in terminal state " + fl::core::to_string(current));
    }
    if (current == fl::core::RunState::kPaused) {
        throw RunManagerError("run is paused; call resume() instead of start()");
    }

    if (current == fl::core::RunState::kCreated) {
        transition(fl::core::RunState::kValidating, "start requested", now_unix_s);
        emit(CoordinatorEventType::kRunValidated, "", now_unix_s);
        transition(fl::core::RunState::kInitializing, "validated", now_unix_s);
        transition(fl::core::RunState::kReady, "initialized", now_unix_s);
        transition(fl::core::RunState::kQueued, "ready", now_unix_s);
    }
    transition(fl::core::RunState::kRunning, "start", now_unix_s);
    emit(CoordinatorEventType::kRunStarted, "", now_unix_s);
}

void RunInstance::pause(const std::string& reason, const std::string& trace_id, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    trace_id_ = trace_id;
    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kPaused) {
        return;  // idempotent
    }
    if (current == fl::core::RunState::kAggregating || current == fl::core::RunState::kEvaluating ||
        current == fl::core::RunState::kCheckpointing) {
        pause_requested_ = true;  // explicit safe-point policy: defer until finalize_round completes
        return;
    }
    if (current == fl::core::RunState::kRunning || current == fl::core::RunState::kWaitingForClients) {
        transition(fl::core::RunState::kPausing, reason, now_unix_s);
        transition(fl::core::RunState::kPaused, reason, now_unix_s);
        emit(CoordinatorEventType::kRunPaused, reason, now_unix_s);
        return;
    }
    throw RunManagerError("cannot pause a run in state " + fl::core::to_string(current));
}

void RunInstance::resume(const std::string& trace_id, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    trace_id_ = trace_id;
    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kRunning || current == fl::core::RunState::kWaitingForClients) {
        return;  // idempotent: already running
    }
    if (current == fl::core::RunState::kCompleted || current == fl::core::RunState::kFailed ||
        current == fl::core::RunState::kCanceled) {
        throw RunManagerError("cannot resume a run in terminal state " + fl::core::to_string(current));
    }
    if (current != fl::core::RunState::kPaused) {
        throw RunManagerError("cannot resume a run in state " + fl::core::to_string(current));
    }
    transition(fl::core::RunState::kQueued, "resume", now_unix_s);
    transition(fl::core::RunState::kRunning, "resume", now_unix_s);
    emit(CoordinatorEventType::kRunResumed, "", now_unix_s);
}

void RunInstance::cancel(const std::string& reason, const std::string& trace_id, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    trace_id_ = trace_id;
    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kCanceled) {
        return;  // idempotent
    }
    if (current == fl::core::RunState::kCompleted || current == fl::core::RunState::kFailed) {
        throw RunManagerError("cannot cancel a run in terminal state " + fl::core::to_string(current));
    }
    if (current == fl::core::RunState::kAggregating || current == fl::core::RunState::kEvaluating ||
        current == fl::core::RunState::kCheckpointing) {
        cancel_requested_ = true;  // explicit safe-point policy
        return;
    }
    if (current == fl::core::RunState::kCreated || current == fl::core::RunState::kValidating ||
        current == fl::core::RunState::kInitializing || current == fl::core::RunState::kReady) {
        transition(fl::core::RunState::kCanceled, reason, now_unix_s);
        emit(CoordinatorEventType::kRunCanceled, reason, now_unix_s);
        return;
    }
    // kQueued, kRunning, kWaitingForClients, kPaused all route through kCanceling.
    transition(fl::core::RunState::kCanceling, reason, now_unix_s);
    transition(fl::core::RunState::kCanceled, reason, now_unix_s);
    emit(CoordinatorEventType::kRunCanceled, reason, now_unix_s);
}

void RunInstance::apply_deferred_safepoint_actions(double now_unix_s) {
    if (cancel_requested_) {
        cancel_requested_ = false;
        pause_requested_ = false;  // cancel wins over a pending pause
        transition(fl::core::RunState::kCanceling, "deferred cancel applied at safe point", now_unix_s);
        transition(fl::core::RunState::kCanceled, "deferred cancel applied at safe point", now_unix_s);
        emit(CoordinatorEventType::kRunCanceled, "deferred cancel applied at safe point", now_unix_s);
        return;
    }
    if (pause_requested_) {
        pause_requested_ = false;
        transition(fl::core::RunState::kPausing, "deferred pause applied at safe point", now_unix_s);
        transition(fl::core::RunState::kPaused, "deferred pause applied at safe point", now_unix_s);
        emit(CoordinatorEventType::kRunPaused, "deferred pause applied at safe point", now_unix_s);
    }
}

void RunInstance::advance(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (dispatcher_) {
        dispatcher_->sweep_expired_leases(now_unix_s);
    }

    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kRunning) {
        if (current_round_id_ >= config_.max_rounds) {
            transition(fl::core::RunState::kCompleted, "max_rounds reached", now_unix_s);
            emit(CoordinatorEventType::kRunCompleted, "", now_unix_s);
            return;
        }
        begin_round(now_unix_s);
        transition(fl::core::RunState::kWaitingForClients, "round dispatched", now_unix_s);
        return;
    }

    if (current == fl::core::RunState::kWaitingForClients) {
        if (!dispatcher_) {
            // dispatcher_ is never checkpointed (see round_results_'s
            // doc comment); after a restore into WAITING_FOR_CLIENTS,
            // reconstruct it before doing anything else.
            rebuild_dispatcher_after_restore(now_unix_s);
        }
        for (const auto& client_id : dispatcher_->failed_client_ids()) {
            failed_clients_.insert(client_id);
            active_leases_.erase(client_id);
        }
        const auto completed = round_results_.size();
        // A round is settled (no more results can possibly arrive) only
        // when every cohort member is accounted for as completed or
        // permanently failed — not when this process's freshly-rebuilt
        // dispatcher_ happens to hold no outstanding tasks, since tasks
        // still leased to a different (possibly still-running) process
        // are deliberately excluded from that rebuild and must not be
        // mistaken for "already resolved" (see
        // rebuild_dispatcher_after_restore).
        const auto settled = (completed + failed_clients_.size()) >= current_cohort_.size();
        if (completed >= config_.minimum_valid_results) {
            finalize_round(now_unix_s);
        } else if (settled) {
            transition(
                fl::core::RunState::kFailed,
                "insufficient valid results for round " + std::to_string(current_round_id_),
                now_unix_s
            );
            emit(CoordinatorEventType::kRunFailed, "insufficient valid results", now_unix_s);
        }
    }
}

namespace {
ClientTaskDescriptor make_descriptor(const RunConfig& config, std::uint64_t round_id, const std::string& model_version, const std::string& client_id) {
    ClientTaskDescriptor descriptor;
    descriptor.run_id = config.run_id;
    descriptor.round_id = round_id;
    descriptor.client_id = client_id;
    descriptor.model_version = model_version;
    descriptor.algorithm = config.algorithm;
    descriptor.dataset_reference = "synthetic:" + client_id;
    descriptor.local_epochs = config.local_epochs;
    descriptor.batch_size = config.batch_size;
    descriptor.learning_rate = config.learning_rate;
    descriptor.momentum = config.momentum;
    descriptor.weight_decay = config.weight_decay;
    descriptor.fedprox_mu = config.fedprox_mu;
    return descriptor;
}
}  // namespace

void RunInstance::begin_round(double now_unix_s) {
    ++current_round_id_;
    current_cohort_ = select_cohort(
        config_.client_ids, current_round_id_, config_.client_selection_seed, config_.target_clients_per_round
    );
    round_results_.clear();
    active_leases_.clear();
    failed_clients_.clear();
    dispatcher_ = std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);

    std::vector<ClientTaskDescriptor> descriptors;
    descriptors.reserve(current_cohort_.size());
    for (const auto& client_id : current_cohort_) {
        descriptors.push_back(make_descriptor(config_, current_round_id_, model_version_, client_id));
    }
    dispatcher_->enqueue(descriptors);

    emit(CoordinatorEventType::kRoundStarted, "", now_unix_s);
    emit(
        CoordinatorEventType::kCohortSelected,
        "",
        now_unix_s,
        {{"cohort_size", std::to_string(current_cohort_.size())}}
    );
}

void RunInstance::rebuild_dispatcher_after_restore(double now_unix_s) {
    dispatcher_ = std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);
    std::vector<ClientTaskDescriptor> descriptors;
    for (const auto& client_id : current_cohort_) {
        if (round_results_.contains(client_id)) {
            continue;  // already submitted; don't re-dispatch
        }
        const auto lease_it = active_leases_.find(client_id);
        if (lease_it != active_leases_.end() && now_unix_s < lease_it->second.lease_expires_at_unix_s) {
            continue;  // still validly leased to another (possibly since-exited) process; don't hand out a duplicate
        }
        if (lease_it != active_leases_.end()) {
            active_leases_.erase(lease_it);  // lease expired: fall through and enqueue fresh for retry
        }
        descriptors.push_back(make_descriptor(config_, current_round_id_, model_version_, client_id));
    }
    dispatcher_->enqueue(descriptors);
}

void RunInstance::finalize_round(double now_unix_s) {
    transition(fl::core::RunState::kAggregating, "", now_unix_s);
    emit(CoordinatorEventType::kAggregationStarted, "", now_unix_s);

    std::vector<ClientResultSubmission> submissions;
    submissions.reserve(round_results_.size());
    for (const auto& [client_id, submission] : round_results_) {
        submissions.push_back(submission);
    }
    std::vector<fl::core::ClientUpdate> updates;
    updates.reserve(submissions.size());
    for (const auto& submission : submissions) {
        updates.push_back(submission.update);
    }

    fl::core::AggregationOptions options;
    options.algorithm = config_.algorithm;
    options.run_id = config_.run_id;
    options.round_id = current_round_id_;
    options.total_clients = config_.total_clients;
    options.weighting = config_.weighting;
    options.contribution_cap = config_.contribution_cap;
    options.server_lr = config_.server_lr;
    options.beta1 = config_.beta1;
    options.beta2 = config_.beta2;
    options.tau = config_.tau;

    const auto aggregator = fl::core::make_aggregator(config_.algorithm);
    const auto result = aggregator->aggregate(config_.manifest, updates, options, optimizer_state_);

    for (const auto& descriptor : config_.manifest.tensors) {
        global_model_.assign(fl::core::add(global_model_.at(descriptor.name), result.model_delta.at(descriptor.name)));
    }
    optimizer_state_ = result.optimizer_state;

    model_version_ = "v" + std::to_string(current_round_id_);
    // Keep the manifest's model_version in lockstep: the next round's
    // tasks are stamped with model_version_ (see begin_round), and
    // UpdateValidator rejects any client update whose base_model_version
    // doesn't exactly match manifest.model_version at aggregation time.
    // Without this, round 2 onward would always be rejected as "stale".
    config_.manifest.model_version = model_version_;

    if (config_.algorithm == fl::core::AggregationAlgorithm::kScaffold) {
        if (scaffold_global_control_.empty()) {
            scaffold_global_control_ = make_zero_collection(config_.manifest);
        }
        for (const auto& descriptor : config_.manifest.tensors) {
            scaffold_global_control_.assign(fl::core::add(
                scaffold_global_control_.at(descriptor.name), result.control_delta.at(descriptor.name)
            ));
        }
        if (scaffold_store_ != nullptr) {
            for (const auto& submission : submissions) {
                if (submission.refreshed_client_control_variate.empty()) {
                    continue;
                }
                ClientAlgorithmState state;
                state.run_id = config_.run_id;
                state.client_id = submission.update.client_id;
                state.algorithm = "scaffold";
                state.model_version = model_version_;
                state.control_variate = submission.refreshed_client_control_variate;
                scaffold_store_->save(config_.run_id, submission.update.client_id, state);
            }
        }
    }

    for (const auto& submission : submissions) {
        if (!submission.update.worker_id.empty()) {
            worker_registry_->record_success(submission.update.worker_id);
        }
    }
    emit(CoordinatorEventType::kAggregationCompleted, "", now_unix_s);
    emit(CoordinatorEventType::kModelVersionUpdated, "", now_unix_s, {{"model_version", model_version_}});

    transition(fl::core::RunState::kCheckpointing, "", now_unix_s);

    // The checkpoint is written *after* the state machine has already
    // moved on to whatever stable resting state (RUNNING or COMPLETED)
    // this round settles into, specifically so a restart recovers into a
    // state advance() knows how to act on, rather than into the
    // momentary CHECKPOINTING state itself. The atomic temp-file+rename
    // write means a crash between these two transitions and the actual
    // save_checkpoint() call below simply loses the last transition (the
    // previous round's checkpoint is still intact and valid) rather than
    // ever exposing a half-written file.
    if (current_round_id_ >= config_.max_rounds) {
        transition(fl::core::RunState::kCompleted, "max_rounds reached", now_unix_s);
        save_checkpoint(now_unix_s);
        emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
        emit(CoordinatorEventType::kRunCompleted, "", now_unix_s);
        return;
    }
    transition(fl::core::RunState::kRunning, "round complete", now_unix_s);
    save_checkpoint(now_unix_s);
    emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
    apply_deferred_safepoint_actions(now_unix_s);
}

std::optional<DispatchedTask> RunInstance::acquire_task(const std::string& worker_id, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_machine_.state() != fl::core::RunState::kWaitingForClients) {
        return std::nullopt;
    }
    if (!dispatcher_) {
        rebuild_dispatcher_after_restore(now_unix_s);
    }
    auto task = dispatcher_->acquire(worker_id, now_unix_s);
    if (task.has_value()) {
        worker_registry_->set_current_task(worker_id, task->task_id);
        active_leases_[task->descriptor.client_id] =
            ActiveLease{worker_id, task->task_id, task->lease_id, task->lease_expires_at_unix_s};
        emit(
            CoordinatorEventType::kTaskAssigned,
            "",
            now_unix_s,
            {{"task_id", task->task_id}, {"client_id", task->descriptor.client_id}}
        );
        // acquire_task does not go through transition(); persist the new
        // lease explicitly so a later, separate process can validate a
        // submission against it (see active_leases_'s doc comment).
        save_checkpoint(now_unix_s);
    }
    return task;
}

std::pair<fl::core::TensorCollection, fl::core::TensorCollection> RunInstance::scaffold_control_variates_for(
    const std::string& client_id
) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (config_.algorithm != fl::core::AggregationAlgorithm::kScaffold) {
        return {fl::core::TensorCollection{}, fl::core::TensorCollection{}};
    }
    auto global = scaffold_global_control_.empty() ? make_zero_collection(config_.manifest) : scaffold_global_control_;

    fl::core::TensorCollection client_control;
    if (scaffold_store_ != nullptr) {
        auto loaded = scaffold_store_->load(config_.run_id, client_id, model_version_);
        if (loaded.has_value()) {
            client_control = loaded->control_variate;
        } else {
            client_control = make_zero_collection(config_.manifest);
        }
    } else {
        client_control = make_zero_collection(config_.manifest);
    }
    return {std::move(global), std::move(client_control)};
}

void RunInstance::report_task_progress(
    const std::string& worker_id, const std::string& task_id, const std::string& lease_id
) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!dispatcher_) {
        throw RunManagerError("no active round to report progress against");
    }
    dispatcher_->report_progress(worker_id, task_id, lease_id);
    emit(CoordinatorEventType::kTaskProgress, "", 0.0, {{"task_id", task_id}});
}

bool RunInstance::submit_client_result(
    const std::string& worker_id,
    const std::string& task_id,
    const std::string& lease_id,
    ClientResultSubmission result,
    double now_unix_s,
    std::string& reason
) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!dispatcher_) {
        rebuild_dispatcher_after_restore(now_unix_s);
    }
    const auto client_id = result.update.client_id;

    if (round_results_.contains(client_id)) {
        reason = "duplicate result: client already has an accepted result for this round";
        worker_registry_->clear_current_task(worker_id);
        emit(CoordinatorEventType::kClientResultRejected, reason, now_unix_s, {{"client_id", client_id}, {"task_id", task_id}});
        save_checkpoint(now_unix_s);
        return false;
    }

    const auto result_copy = result;  // may be needed after result is moved-from below
    bool accepted = false;

    const auto lease_it = active_leases_.find(client_id);
    if (lease_it != active_leases_.end()) {
        // Authoritative path. dispatcher_ is rebuilt fresh on essentially
        // every call in the CLI-bridge model (see
        // rebuild_dispatcher_after_restore), so its in-memory task_id/
        // lease_id values are only unique *within one rebuild*, not
        // globally: a different, still-pending client can coincidentally
        // be assigned the exact same task_id string in a later rebuild.
        // Trusting dispatcher_'s own lookup here could therefore validate
        // a submission against the *wrong client's* task. active_leases_
        // is checkpointed and keyed by client_id, so it is what's
        // actually authoritative for "does this submission belong to the
        // lease it claims."
        const auto& lease = lease_it->second;
        if (now_unix_s > lease.lease_expires_at_unix_s) {
            reason = "late result: lease already expired";
        } else if (lease.worker_id != worker_id || lease.task_id != task_id || lease.lease_id != lease_id) {
            reason = "lease mismatch: result does not belong to the current lease holder";
        } else {
            accepted = true;
        }
    } else {
        // No checkpointed lease for this client at all — the path a
        // genuinely long-lived, single-process server would always take
        // (dispatcher_ never rebuilt, so its bookkeeping is trustworthy).
        accepted = dispatcher_->submit_result(worker_id, task_id, lease_id, std::move(result), now_unix_s, reason);
    }

    worker_registry_->clear_current_task(worker_id);
    if (accepted) {
        round_results_[client_id] = result_copy;
        active_leases_.erase(client_id);
        emit(CoordinatorEventType::kClientResultAccepted, "", now_unix_s, {{"client_id", client_id}, {"task_id", task_id}});
    } else {
        worker_registry_->record_failure(worker_id);
        emit(
            CoordinatorEventType::kClientResultRejected,
            reason,
            now_unix_s,
            {{"client_id", client_id}, {"task_id", task_id}}
        );
    }
    // submit_client_result does not go through transition() (the run
    // stays in WAITING_FOR_CLIENTS throughout a round), so it must
    // explicitly checkpoint round_results_ itself, or an accepted result
    // would be lost on a restart before the round finalizes.
    save_checkpoint(now_unix_s);
    return accepted;
}

std::string RunInstance::checkpoint_path() const {
    return (std::filesystem::path(checkpoint_directory_) / (config_.run_id + ".checkpoint")).string();
}

void RunInstance::save_checkpoint(double now_unix_s) const {
    std::ostringstream body;
    body << "schema_version=1\n";
    body << "run_id=" << config_.run_id << "\n";
    body << "run_state=" << fl::core::to_string(state_machine_.state()) << "\n";
    body << "current_round=" << current_round_id_ << "\n";
    body << "max_rounds=" << config_.max_rounds << "\n";
    body << "model_version=" << model_version_ << "\n";
    body << "algorithm=" << fl::core::to_string(config_.algorithm) << "\n";
    body << "saved_at=" << format_iso8601(now_unix_s) << "\n";
    write_collection(body, "global_model", global_model_);
    write_collection(body, "optimizer_first_moment", optimizer_state_.first_moment);
    write_collection(body, "optimizer_second_moment", optimizer_state_.second_moment);
    body << "optimizer_step=" << optimizer_state_.step << "\n";
    write_collection(body, "scaffold_control", scaffold_global_control_);

    body << "cohort_count=" << current_cohort_.size() << "\n";
    for (const auto& client_id : current_cohort_) {
        body << "cohort_client=" << client_id << "\n";
    }
    body << "round_result_count=" << round_results_.size() << "\n";
    for (const auto& [client_id, submission] : round_results_) {
        body << "round_result=" << encode_round_result(client_id, submission) << "\n";
    }
    body << "active_lease_count=" << active_leases_.size() << "\n";
    for (const auto& [client_id, lease] : active_leases_) {
        body << "active_lease=" << client_id << "\t" << lease.worker_id << "\t" << lease.task_id << "\t"
             << lease.lease_id << "\t" << std::setprecision(17) << lease.lease_expires_at_unix_s << "\n";
    }
    body << "failed_client_count=" << failed_clients_.size() << "\n";
    for (const auto& client_id : failed_clients_) {
        body << "failed_client=" << client_id << "\n";
    }

    const auto body_str = body.str();
    std::ostringstream out;
    out << body_str;
    out << "checksum=" << hash_to_hex(fnv1a_hash(body_str)) << "\n";

    std::filesystem::create_directories(checkpoint_directory_);
    const auto path = checkpoint_path();
    const auto temp_path = path + ".tmp";
    {
        std::ofstream file(temp_path, std::ios::binary | std::ios::trunc);
        if (!file) {
            throw std::runtime_error("failed to open coordinator checkpoint temp file: " + temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw std::runtime_error("failed to write coordinator checkpoint temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, path, error_code);
    if (error_code) {
        std::filesystem::remove(path, error_code);
        std::filesystem::rename(temp_path, path, error_code);
        if (error_code) {
            throw std::runtime_error("failed to atomically move coordinator checkpoint into place: " + error_code.message());
        }
    }
}

void RunInstance::restore_from_checkpoint() {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto path = checkpoint_path();
    if (!std::filesystem::exists(path)) {
        throw std::runtime_error("no coordinator checkpoint found at " + path);
    }
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("failed to open coordinator checkpoint: " + path);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const auto payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw std::runtime_error("coordinator checkpoint truncated: missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    std::string checksum_line = payload.substr(marker + 1);
    const auto equals = checksum_line.find('=');
    std::string checksum_value = equals == std::string::npos ? "" : checksum_line.substr(equals + 1);
    while (!checksum_value.empty() && (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum_value) {
        throw std::runtime_error("coordinator checkpoint checksum mismatch: file is corrupt or was truncated");
    }

    std::vector<std::pair<std::string, std::string>> fields;
    std::stringstream stream(body);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty()) continue;
        const auto position = line.find('=');
        if (position == std::string::npos) {
            throw std::runtime_error("invalid coordinator checkpoint line: " + line);
        }
        fields.emplace_back(line.substr(0, position), line.substr(position + 1));
    }

    auto read_collection = [&fields](const std::string& key) {
        fl::core::TensorCollection collection;
        std::size_t expected = 0;
        std::size_t found = 0;
        for (const auto& [field_key, value] : fields) {
            if (field_key == key + "_count") {
                expected = std::stoull(value);
            } else if (field_key == key + "_tensor") {
                collection.insert(parse_tensor_field(value));
                ++found;
            }
        }
        if (found != expected) {
            throw std::runtime_error("coordinator checkpoint truncated for " + key);
        }
        return collection;
    };

    for (const auto& [key, value] : fields) {
        if (key == "run_id" && value != config_.run_id) {
            throw std::runtime_error("coordinator checkpoint run_id mismatch");
        } else if (key == "current_round") {
            current_round_id_ = std::stoull(value);
        } else if (key == "model_version") {
            model_version_ = value;
            // Keep in lockstep with finalize_round's invariant: the
            // manifest's model_version must always match model_version_,
            // or the next round's UpdateValidator call rejects every
            // client update as stale.
            config_.manifest.model_version = value;
        } else if (key == "optimizer_step") {
            optimizer_state_.step = std::stoull(value);
        } else if (key == "run_state") {
            const auto restored_state = fl::core::run_state_from_string(value);
            // RunStateMachine has no direct "force state" API by design
            // (every transition must be validated and recorded); recovery
            // reconstructs the same effective state via the one
            // constructor overload that accepts an initial state, which
            // is exactly what a fresh-process restart needs.
            state_machine_ = fl::core::RunStateMachine(restored_state);
        }
    }

    global_model_ = read_collection("global_model");
    optimizer_state_.first_moment = read_collection("optimizer_first_moment");
    optimizer_state_.second_moment = read_collection("optimizer_second_moment");
    scaffold_global_control_ = read_collection("scaffold_control");

    current_cohort_.clear();
    for (const auto& [key, value] : fields) {
        if (key == "cohort_client") {
            current_cohort_.push_back(value);
        }
    }

    round_results_.clear();
    std::size_t expected_results = 0;
    std::size_t found_results = 0;
    for (const auto& [key, value] : fields) {
        if (key == "round_result_count") {
            expected_results = std::stoull(value);
        } else if (key == "round_result") {
            auto [client_id, submission] = parse_round_result(value);
            round_results_[client_id] = std::move(submission);
            ++found_results;
        }
    }
    if (found_results != expected_results) {
        throw std::runtime_error("coordinator checkpoint truncated for round_result");
    }

    active_leases_.clear();
    std::size_t expected_leases = 0;
    std::size_t found_leases = 0;
    for (const auto& [key, value] : fields) {
        if (key == "active_lease_count") {
            expected_leases = std::stoull(value);
        } else if (key == "active_lease") {
            const auto parts = split(value, '\t');
            if (parts.size() != 5) {
                throw std::runtime_error("malformed active_lease checkpoint line");
            }
            active_leases_[parts[0]] = ActiveLease{parts[1], parts[2], parts[3], std::stod(parts[4])};
            ++found_leases;
        }
    }
    if (found_leases != expected_leases) {
        throw std::runtime_error("coordinator checkpoint truncated for active_lease");
    }

    failed_clients_.clear();
    std::size_t expected_failed = 0;
    std::size_t found_failed = 0;
    for (const auto& [key, value] : fields) {
        if (key == "failed_client_count") {
            expected_failed = std::stoull(value);
        } else if (key == "failed_client") {
            failed_clients_.insert(value);
            ++found_failed;
        }
    }
    if (found_failed != expected_failed) {
        throw std::runtime_error("coordinator checkpoint truncated for failed_client");
    }

    // dispatcher_ is intentionally left null here; the next advance() or
    // acquire_task()/submit_client_result() call rebuilds it (see
    // rebuild_dispatcher_after_restore) using current_cohort_ and
    // round_results_ above.
    dispatcher_.reset();
}

// ------------------------------------------------------------------ //
// RunManager
// ------------------------------------------------------------------ //

RunManager::RunManager(
    CoordinatorConfig config, std::string checkpoint_root_directory, std::string scaffold_state_root_directory
)
    : config_(config),
      checkpoint_root_directory_(std::move(checkpoint_root_directory)),
      scaffold_state_root_directory_(std::move(scaffold_state_root_directory)),
      worker_registry_(config.missed_heartbeat_threshold, config.default_heartbeat_interval_seconds),
      event_bus_(config.event_bus_capacity_per_run),
      scaffold_store_(std::make_unique<FilesystemClientAlgorithmStateStore>(scaffold_state_root_directory_)) {}

std::string RunManager::create_run(RunConfig config, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (runs_.contains(config.run_id)) {
        throw RunManagerError("duplicate run_id: " + config.run_id);
    }
    if (runs_.size() >= config_.max_concurrent_runs) {
        throw RunManagerError("maximum concurrent run limit reached: " + std::to_string(config_.max_concurrent_runs));
    }
    const auto run_id = config.run_id;
    auto instance = std::make_unique<RunInstance>(
        config, config_, event_bus_, worker_registry_, scaffold_store_.get(), checkpoint_root_directory_
    );
    CoordinatorEvent event;
    event.run_id = run_id;
    event.type = CoordinatorEventType::kRunCreated;
    auto published = event_bus_.publish(std::move(event), format_iso8601(now_unix_s));
    log_event(published);
    runs_[run_id] = std::move(instance);
    return run_id;
}

RunInstance& RunManager::get(const std::string& run_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = runs_.find(run_id);
    if (it == runs_.end()) {
        throw RunManagerError("unknown run_id: " + run_id);
    }
    return *it->second;
}

const RunInstance& RunManager::get(const std::string& run_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = runs_.find(run_id);
    if (it == runs_.end()) {
        throw RunManagerError("unknown run_id: " + run_id);
    }
    return *it->second;
}

std::vector<std::string> RunManager::list_run_ids() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> ids;
    ids.reserve(runs_.size());
    for (const auto& [run_id, instance] : runs_) {
        ids.push_back(run_id);
    }
    return ids;
}

}  // namespace fl::coordinator
