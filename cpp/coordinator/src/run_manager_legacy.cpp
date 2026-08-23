#include "fl_coordinator/run_manager.hpp"

#include "fl_coordinator/structured_log.hpp"
#include "fl_core/secure_random.hpp"

#include <algorithm>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
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

void write_collection(std::ostringstream& out,
                      const std::string& key,
                      const fl::core::TensorCollection& collection) {
    out << key << "_count=" << collection.tensors().size() << "\n";
    for (const auto& [name, tensor] : collection.tensors()) {
        out << key << "_tensor=" << name << "|f32|";
        const auto& shape = tensor.descriptor().shape;
        for (std::size_t index = 0; index < shape.size(); ++index) {
            if (index > 0)
                out << "-";
            out << shape[index];
        }
        out << "|";
        const auto& values = tensor.values();
        for (std::size_t index = 0; index < values.size(); ++index) {
            if (index > 0)
                out << ",";
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
        if (index > 0)
            out << "-";
        out << shape[index];
    }
    out << "|";
    const auto& values = tensor.values();
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0)
            out << ",";
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
        if (!first)
            out << ";";
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

std::string encode_round_result(const std::string& client_id,
                                const ClientResultSubmission& submission) {
    const auto& update = submission.update;
    std::ostringstream out;
    out << client_id << "\t" << update.run_id << "\t" << update.round_id << "\t"
        << update.base_model_version << "\t" << fl::core::to_string(update.algorithm) << "\t"
        << update.sample_count << "\t" << update.nonce << "\t" << update.update_id << "\t"
        << update.worker_id << "\t" << join_tensor_collection(update.delta) << "\t"
        << join_tensor_collection(update.control_delta) << "\t"
        << join_tensor_collection(submission.refreshed_client_control_variate);
    return out.str();
}

fl::core::AggregationAlgorithm algorithm_from_string(const std::string& value) {
    if (value == "fedavg")
        return fl::core::AggregationAlgorithm::kFedAvg;
    if (value == "fedprox")
        return fl::core::AggregationAlgorithm::kFedProx;
    if (value == "scaffold")
        return fl::core::AggregationAlgorithm::kScaffold;
    if (value == "fedadagrad")
        return fl::core::AggregationAlgorithm::kFedAdagrad;
    if (value == "fedadam")
        return fl::core::AggregationAlgorithm::kFedAdam;
    if (value == "fedyogi")
        return fl::core::AggregationAlgorithm::kFedYogi;
    if (value == "fedsam")
        return fl::core::AggregationAlgorithm::kFedSam;
    if (value == "ditto")
        return fl::core::AggregationAlgorithm::kDitto;
    if (value == "per_fedavg")
        return fl::core::AggregationAlgorithm::kPerFedAvg;
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

// the Algorithm Expansion phase: personalization_metrics_by_client_ is checkpointed the
// same way round_results_/active_leases_ are — the CLI bridge's
// process-per-call model means anything held only in memory is invisible
// to the next call (discovered by actually exercising
// get-personalization-summary across two separate CLI invocations; see
// docs/personalized-evaluation.md). algorithm_metrics (a variable-size
// map) is deliberately not persisted here — only the fixed scalar fields
// that GetPersonalizationSummary callers actually need survive a
// coordinator restart.
std::string encode_personalization_metric(const PersonalizationMetricRecord& record) {
    std::ostringstream out;
    out << record.client_id << "\t" << record.round_id << "\t" << record.algorithm << "\t"
        << std::setprecision(17) << record.global_local_accuracy << "\t"
        << record.personalized_local_accuracy << "\t" << record.global_local_loss << "\t"
        << record.personalized_local_loss << "\t" << record.sample_count << "\t"
        << record.personalized_improvement << "\t" << record.personalized_model_version << "\t"
        << record.recorded_at << "\t" << (record.has_personalized_model ? 1 : 0);
    return out.str();
}

std::pair<std::string, PersonalizationMetricRecord> parse_personalization_metric(
    const std::string& line) {
    const auto parts = split(line, '\t');
    if (parts.size() != 12) {
        throw std::runtime_error("malformed personalization_metric checkpoint line");
    }
    PersonalizationMetricRecord record;
    record.client_id = parts[0];
    record.round_id = std::stoull(parts[1]);
    record.algorithm = parts[2];
    record.global_local_accuracy = std::stod(parts[3]);
    record.personalized_local_accuracy = std::stod(parts[4]);
    record.global_local_loss = std::stod(parts[5]);
    record.personalized_local_loss = std::stod(parts[6]);
    record.sample_count = std::stoull(parts[7]);
    record.personalized_improvement = std::stod(parts[8]);
    record.personalized_model_version = static_cast<std::uint32_t>(std::stoul(parts[9]));
    record.recorded_at = parts[10];
    record.has_personalized_model = parts[11] == "1";
    return {parts[0], record};
}

// Privacy Engineering phase: checkpoint encode/parse helpers for the
// three independent privacy ledgers (docs/privacy-ledger.md) — same
// tab-separated-line convention as encode_round_result/
// encode_personalization_metric above. Persisted so a coordinator
// restart doesn't silently lose accounting history that
// GetPrivacyLedger/GetPrivacyMetrics callers depend on for audit
// purposes — see docs/coordinator-recovery.md.
std::string encode_sample_level_entry(const SampleLevelLedgerEntry& entry) {
    std::ostringstream out;
    out << entry.run_id << "\t" << entry.round_id << "\t" << entry.client_id << "\t"
        << std::setprecision(17) << entry.epsilon << "\t" << entry.delta << "\t"
        << entry.noise_multiplier << "\t" << entry.sample_rate << "\t" << entry.steps << "\t"
        << entry.accountant << "\t" << entry.recorded_at << "\t" << entry.entry_id;
    return out.str();
}

SampleLevelLedgerEntry parse_sample_level_entry(const std::string& line) {
    const auto parts = split(line, '\t');
    if (parts.size() != 11) {
        throw std::runtime_error("malformed sample_level_entry checkpoint line");
    }
    SampleLevelLedgerEntry entry;
    entry.run_id = parts[0];
    entry.round_id = std::stoull(parts[1]);
    entry.client_id = parts[2];
    entry.epsilon = std::stod(parts[3]);
    entry.delta = std::stod(parts[4]);
    entry.noise_multiplier = std::stod(parts[5]);
    entry.sample_rate = std::stod(parts[6]);
    entry.steps = std::stoull(parts[7]);
    entry.accountant = parts[8];
    entry.recorded_at = parts[9];
    entry.entry_id = parts[10];
    return entry;
}

std::string encode_user_level_entry(const UserLevelLedgerEntry& entry) {
    std::ostringstream out;
    out << entry.run_id << "\t" << entry.round_id << "\t" << std::setprecision(17) << entry.epsilon
        << "\t" << entry.delta << "\t" << entry.noise_multiplier << "\t" << entry.clipping_bound
        << "\t" << entry.num_clients << "\t" << std::setprecision(17) << entry.committed_at_unix_s;
    return out.str();
}

UserLevelLedgerEntry parse_user_level_entry(const std::string& line) {
    const auto parts = split(line, '\t');
    // Secure User-Level DP Operations, Observability, and Release
    // Evidence slice, Work Area G: 8 fields as of this slice
    // (committed_at_unix_s appended) -- a checkpoint written by a
    // binary predating this field has only 7 and is treated as
    // malformed, matching this format's existing "no versioned
    // migration policy" precedent (every other checkpoint line in this
    // file has the same strict-count-or-throw behavior, not a
    // best-effort partial parse).
    if (parts.size() != 8) {
        throw std::runtime_error("malformed user_level_entry checkpoint line");
    }
    UserLevelLedgerEntry entry;
    entry.run_id = parts[0];
    entry.round_id = std::stoull(parts[1]);
    entry.epsilon = std::stod(parts[2]);
    entry.delta = std::stod(parts[3]);
    entry.noise_multiplier = std::stod(parts[4]);
    entry.clipping_bound = std::stod(parts[5]);
    entry.num_clients = static_cast<std::uint32_t>(std::stoul(parts[6]));
    entry.committed_at_unix_s = std::stod(parts[7]);
    return entry;
}

std::string encode_clipping_entry(const AdaptiveClippingLedgerEntry& entry) {
    std::ostringstream out;
    out << entry.run_id << "\t" << entry.round_id << "\t" << std::setprecision(17) << entry.epsilon
        << "\t" << entry.delta << "\t" << entry.clip_value << "\t"
        << entry.noisy_over_threshold_fraction;
    return out.str();
}

AdaptiveClippingLedgerEntry parse_clipping_entry(const std::string& line) {
    const auto parts = split(line, '\t');
    if (parts.size() != 6) {
        throw std::runtime_error("malformed clipping_entry checkpoint line");
    }
    AdaptiveClippingLedgerEntry entry;
    entry.run_id = parts[0];
    entry.round_id = std::stoull(parts[1]);
    entry.epsilon = std::stod(parts[2]);
    entry.delta = std::stod(parts[3]);
    entry.clip_value = std::stod(parts[4]);
    entry.noisy_over_threshold_fraction = std::stod(parts[5]);
    return entry;
}

}  // namespace

RunManagerError::RunManagerError(const std::string& what) : std::runtime_error(what) {}

// ------------------------------------------------------------------ //
// RunInstance
// ------------------------------------------------------------------ //

RunInstance::RunInstance(RunConfig config,
                         CoordinatorConfig coordinator_config,
                         EventBus& event_bus,
                         WorkerRegistry& worker_registry,
                         ClientAlgorithmStateStore* scaffold_store,
                         std::string checkpoint_directory)
    : config_(std::move(config)),
      coordinator_config_(coordinator_config),
      event_bus_(&event_bus),
      worker_registry_(&worker_registry),
      scaffold_store_(scaffold_store),
      checkpoint_directory_(std::move(checkpoint_directory)),
      global_model_(make_zero_collection(config_.manifest)) {
    // Privacy Engineering phase: see docs/user-level-dp.md. Sample rate
    // is the client-level subsampling rate (target cohort size / total
    // client pool) — the same q the subsampled-Gaussian RDP formula
    // expects. Constructed only for privacy modes that actually use it,
    // so every pre-existing non-private run's behavior (and cost) is
    // completely unchanged.
    if (config_.privacy_mode == fl::core::PrivacyMode::kUserLevelDp ||
        config_.privacy_mode == fl::core::PrivacyMode::kHybridDp) {
        const double sample_rate = config_.total_clients > 0
                                       ? static_cast<double>(config_.target_clients_per_round) /
                                             static_cast<double>(config_.total_clients)
                                       : 0.0;
        user_level_accountant_ = std::make_unique<fl::core::UserLevelAccountant>(
            config_.user_level_privacy.noise_multiplier,
            sample_rate,
            config_.user_level_privacy.target_delta);
        if (config_.privacy_noise_seed != 0) {
            noise_provider_ =
                std::make_unique<fl::core::DeterministicNoiseProvider>(config_.privacy_noise_seed);
        } else {
            // Secure Transport and Worker Identity Hardening slice: the
            // live runtime default is now CryptoSecureNoiseProvider
            // (fl_core/secure_random.hpp), backed by
            // OsEntropySecureRandomProvider — real, buffered OS-CSPRNG
            // bytes on every draw, not SecureNoiseProvider's single
            // std::random_device-seeded mt19937_64 reused for the whole
            // run. See docs/secure-random-runtime.md.
            noise_provider_ = std::make_unique<fl::core::CryptoSecureNoiseProvider>();
        }
        if (config_.adaptive_clipping_enabled) {
            // Reuses the same noise_provider_ as central Gaussian noise —
            // both draws happen on the same trusted coordinator path, and
            // a single provider keeps construction/ownership simple. The
            // two draws are still separately accounted (this constructs
            // its own AdaptiveClipController, which owns its own
            // UserLevelAccountant instance internally) — see the Critical
            // Privacy Rule note in fl_core/privacy.hpp.
            adaptive_clip_controller_ = std::make_unique<fl::core::AdaptiveClipController>(
                config_.adaptive_clipping, *noise_provider_);
        }
    }
}

namespace {
double budget_remaining(double epsilon_budget, double current_epsilon) {
    // 0 (unset) means no budget was configured at all — reported as
    // +infinity ("unbounded remaining"), never as 0 or a negative
    // number, so a caller can't mistake "no budget configured" for
    // "budget exhausted". See *DPConfig::epsilon_budget's doc comment.
    if (epsilon_budget <= 0.0) {
        return std::numeric_limits<double>::infinity();
    }
    return epsilon_budget - current_epsilon;
}
}  // namespace

PrivacyMetricsSnapshot RunInstance::privacy_metrics_snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    PrivacyMetricsSnapshot snapshot;
    snapshot.run_id = config_.run_id;
    snapshot.round_id = current_round_id_;

    if (!sample_level_ledger_.empty()) {
        snapshot.has_sample_level = true;
        // Worst-case (max) epsilon across clients so far — see the
        // struct's doc comment for why this is the chosen reduction.
        double max_epsilon = 0.0;
        for (const auto& entry : sample_level_ledger_) {
            max_epsilon = std::max(max_epsilon, entry.epsilon);
        }
        snapshot.sample_epsilon = max_epsilon;
        snapshot.sample_delta = sample_level_ledger_.back().delta;
    }
    if (!user_level_ledger_.empty()) {
        snapshot.has_user_level = true;
        snapshot.user_epsilon = user_level_ledger_.back().epsilon;
        snapshot.user_delta = user_level_ledger_.back().delta;
    }
    if (!adaptive_clipping_ledger_.empty()) {
        snapshot.has_clipping = true;
        snapshot.clipping_epsilon = adaptive_clipping_ledger_.back().epsilon;
        snapshot.clipping_delta = adaptive_clipping_ledger_.back().delta;
    }
    snapshot.current_clip_value = adaptive_clip_controller_ != nullptr
                                      ? adaptive_clip_controller_->clip_value()
                                      : config_.user_level_privacy.initial_clipping_bound;
    return snapshot;
}

PrivacyProjection RunInstance::privacy_projection() const {
    std::lock_guard<std::mutex> lock(mutex_);
    PrivacyProjection projection;

    if (!sample_level_ledger_.empty()) {
        projection.has_sample_level = true;
        double max_epsilon = 0.0;
        for (const auto& entry : sample_level_ledger_) {
            max_epsilon = std::max(max_epsilon, entry.epsilon);
        }
        projection.sample_current_epsilon = max_epsilon;
        // The coordinator does not own a sample-level accountant (that
        // state lives entirely in each Python worker's Opacus instance
        // — see docs/hybrid-dp.md) so a genuine one-step-ahead
        // projection isn't computable here. Reporting current_epsilon
        // unchanged is a documented limitation, not a fabricated
        // forecast — see docs/known-limitations.md.
        projection.sample_projected_next_epsilon = max_epsilon;
        projection.sample_budget_remaining =
            budget_remaining(config_.sample_level_privacy.epsilon_budget, max_epsilon);
    }
    if (user_level_accountant_ != nullptr) {
        projection.has_user_level = true;
        const double current = user_level_accountant_->get_epsilon();
        projection.user_current_epsilon = current;
        projection.user_projected_next_epsilon = user_level_accountant_->project_epsilon(1);
        projection.user_budget_remaining =
            budget_remaining(config_.user_level_privacy.epsilon_budget, current);
    }
    if (adaptive_clip_controller_ != nullptr) {
        projection.has_clipping = true;
        const double current = adaptive_clip_controller_->epsilon();
        projection.clipping_current_epsilon = current;
        projection.clipping_projected_next_epsilon =
            adaptive_clip_controller_->projected_epsilon_after_one_more_round();
        projection.clipping_budget_remaining =
            budget_remaining(config_.adaptive_clipping.epsilon_budget, current);
    }
    return projection;
}

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

std::vector<PersonalizationMetricRecord> RunInstance::personalization_summary() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<PersonalizationMetricRecord> records;
    records.reserve(personalization_metrics_by_client_.size());
    for (const auto& [client_id, record] : personalization_metrics_by_client_) {
        records.push_back(record);
    }
    return records;
}

void RunInstance::transition(fl::core::RunState next,
                             const std::string& reason,
                             double now_unix_s) {
    state_machine_.transition_to(next, reason, format_iso8601(now_unix_s));
    // Every transition persists a checkpoint, not just round finalization
    // — otherwise a restart between (say) StartRun and the first round
    // completing would forget that the run had been started at all. This
    // is what makes lifecycle actions (start/pause/resume/cancel) durable
    // across a coordinator restart, not just round progress.
    save_checkpoint(now_unix_s);
}

CoordinatorEvent RunInstance::emit(CoordinatorEventType type,
                                   const std::string& reason,
                                   double now_unix_s,
                                   std::map<std::string, std::string> metadata) {
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

    if (current == fl::core::RunState::kRunning ||
        current == fl::core::RunState::kWaitingForClients ||
        current == fl::core::RunState::kAggregating || current == fl::core::RunState::kEvaluating ||
        current == fl::core::RunState::kCheckpointing) {
        return;  // idempotent: already running, never start a second execution loop
    }
    if (current == fl::core::RunState::kCompleted || current == fl::core::RunState::kFailed ||
        current == fl::core::RunState::kCanceled) {
        throw RunManagerError("cannot start a run in terminal state " +
                              fl::core::to_string(current));
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
        pause_requested_ =
            true;  // explicit safe-point policy: defer until finalize_round completes
        return;
    }
    if (current == fl::core::RunState::kRunning ||
        current == fl::core::RunState::kWaitingForClients) {
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

    if (current == fl::core::RunState::kRunning ||
        current == fl::core::RunState::kWaitingForClients) {
        return;  // idempotent: already running
    }
    if (current == fl::core::RunState::kCompleted || current == fl::core::RunState::kFailed ||
        current == fl::core::RunState::kCanceled) {
        throw RunManagerError("cannot resume a run in terminal state " +
                              fl::core::to_string(current));
    }
    if (current != fl::core::RunState::kPaused) {
        throw RunManagerError("cannot resume a run in state " + fl::core::to_string(current));
    }
    transition(fl::core::RunState::kQueued, "resume", now_unix_s);
    transition(fl::core::RunState::kRunning, "resume", now_unix_s);
    emit(CoordinatorEventType::kRunResumed, "", now_unix_s);
}

void RunInstance::cancel(const std::string& reason,
                         const std::string& trace_id,
                         double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    trace_id_ = trace_id;
    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kCanceled) {
        return;  // idempotent
    }
    if (current == fl::core::RunState::kCompleted || current == fl::core::RunState::kFailed) {
        throw RunManagerError("cannot cancel a run in terminal state " +
                              fl::core::to_string(current));
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
        transition(
            fl::core::RunState::kCanceling, "deferred cancel applied at safe point", now_unix_s);
        transition(
            fl::core::RunState::kCanceled, "deferred cancel applied at safe point", now_unix_s);
        emit(CoordinatorEventType::kRunCanceled,
             "deferred cancel applied at safe point",
             now_unix_s);
        return;
    }
    if (pause_requested_) {
        pause_requested_ = false;
        transition(
            fl::core::RunState::kPausing, "deferred pause applied at safe point", now_unix_s);
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
            transition(fl::core::RunState::kFailed,
                       "insufficient valid results for round " + std::to_string(current_round_id_),
                       now_unix_s);
            emit(CoordinatorEventType::kRunFailed, "insufficient valid results", now_unix_s);
        }
    }
}

namespace {
ClientTaskDescriptor make_descriptor(const RunConfig& config,
                                     std::uint64_t round_id,
                                     const std::string& model_version,
                                     const std::string& client_id) {
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
    if (config.privacy_mode == fl::core::PrivacyMode::kSampleLevelDp ||
        config.privacy_mode == fl::core::PrivacyMode::kHybridDp) {
        descriptor.sample_level_dp_active = true;
        descriptor.sample_level_privacy = config.sample_level_privacy;
    }
    return descriptor;
}
}  // namespace

void RunInstance::begin_round(double now_unix_s) {
    ++current_round_id_;
    current_cohort_ = select_cohort(config_.client_ids,
                                    current_round_id_,
                                    config_.client_selection_seed,
                                    config_.target_clients_per_round);
    round_results_.clear();
    active_leases_.clear();
    failed_clients_.clear();
    dispatcher_ =
        std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);

    std::vector<ClientTaskDescriptor> descriptors;
    descriptors.reserve(current_cohort_.size());
    for (const auto& client_id : current_cohort_) {
        descriptors.push_back(
            make_descriptor(config_, current_round_id_, model_version_, client_id));
    }
    dispatcher_->enqueue(descriptors);

    emit(CoordinatorEventType::kRoundStarted, "", now_unix_s);
    emit(CoordinatorEventType::kCohortSelected,
         "",
         now_unix_s,
         {{"cohort_size", std::to_string(current_cohort_.size())}});
}

void RunInstance::rebuild_dispatcher_after_restore(double now_unix_s) {
    dispatcher_ =
        std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);
    std::vector<ClientTaskDescriptor> descriptors;
    for (const auto& client_id : current_cohort_) {
        if (round_results_.contains(client_id)) {
            continue;  // already submitted; don't re-dispatch
        }
        const auto lease_it = active_leases_.find(client_id);
        if (lease_it != active_leases_.end() &&
            now_unix_s < lease_it->second.lease_expires_at_unix_s) {
            continue;  // still validly leased to another (possibly since-exited) process; don't
                       // hand out a duplicate
        }
        if (lease_it != active_leases_.end()) {
            active_leases_.erase(
                lease_it);  // lease expired: fall through and enqueue fresh for retry
        }
        descriptors.push_back(
            make_descriptor(config_, current_round_id_, model_version_, client_id));
    }
    dispatcher_->enqueue(descriptors);
}

void RunInstance::finalize_round(double now_unix_s) {
    // Privacy budget policy — kStopBeforeExceeding pre-check (docs/
    // privacy-budget-policies.md): the ONLY policy checked before any
    // work happens, using each mechanism's PROJECTED (not yet applied)
    // epsilon. If applying either mechanism this round would meet-or-
    // exceed its configured budget, refuse to release this round's
    // aggregate at all — the run ends now, with this round's already-
    // collected client results simply never aggregated. This is the
    // only policy that guarantees a budget is never actually exceeded;
    // kStopAfterCurrentRound/kFailRun are checked reactively, below,
    // after this round's real epsilon is known.
    if (config_.privacy_budget_policy == fl::core::PrivacyBudgetPolicy::kStopBeforeExceeding) {
        std::string exceeded_mechanism;
        if (user_level_accountant_ != nullptr && config_.user_level_privacy.epsilon_budget > 0.0 &&
            user_level_accountant_->project_epsilon(1) >=
                config_.user_level_privacy.epsilon_budget) {
            exceeded_mechanism = "user_level";
        } else if (adaptive_clip_controller_ != nullptr &&
                   config_.adaptive_clipping.epsilon_budget > 0.0 &&
                   adaptive_clip_controller_->projected_epsilon_after_one_more_round() >=
                       config_.adaptive_clipping.epsilon_budget) {
            exceeded_mechanism = "clipping";
        }
        if (!exceeded_mechanism.empty()) {
            emit(CoordinatorEventType::kPrivacyBudgetExceeded,
                 "privacy budget would be exceeded by this round; round not released",
                 now_unix_s,
                 {{"mechanism", exceeded_mechanism},
                  {"policy", fl::core::to_string(config_.privacy_budget_policy)}});
            // RunStateMachine has no direct WAITING_FOR_CLIENTS ->
            // COMPLETED transition (see allowed_next_states in
            // coordinator.cpp) — must pass through AGGREGATING ->
            // CHECKPOINTING first, same path the normal end-of-run flow
            // below takes, just without ever touching global_model_ or
            // releasing this round's aggregate.
            transition(fl::core::RunState::kAggregating,
                       "privacy budget (" + exceeded_mechanism + ") would be exceeded",
                       now_unix_s);
            transition(fl::core::RunState::kCheckpointing, "", now_unix_s);
            transition(fl::core::RunState::kCompleted,
                       "privacy budget (" + exceeded_mechanism + ") would be exceeded",
                       now_unix_s);
            save_checkpoint(now_unix_s);
            emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
            emit(CoordinatorEventType::kRunCompleted, "", now_unix_s);
            return;
        }
    }

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

    const bool user_level_dp_active =
        (config_.privacy_mode == fl::core::PrivacyMode::kUserLevelDp ||
         config_.privacy_mode == fl::core::PrivacyMode::kHybridDp) &&
        user_level_accountant_ != nullptr;

    // Clip bound actually used THIS round: fixed at
    // user_level_privacy.initial_clipping_bound unless adaptive clipping
    // is enabled, in which case it's whatever AdaptiveClipController
    // computed at the end of the previous round (or its own
    // initial_clip on round 1) — see docs/adaptive-clipping.md.
    const double clip_bound_this_round = adaptive_clip_controller_ != nullptr
                                             ? adaptive_clip_controller_->clip_value()
                                             : config_.user_level_privacy.initial_clipping_bound;
    std::uint64_t over_threshold_count = 0;

    if (user_level_dp_active) {
        // Clip every client's complete round contribution to L2 norm
        // clip_bound_this_round BEFORE aggregation — see
        // docs/user-level-dp.md. shared_parameter_names from the
        // aggregation manifest enforces local-head exclusion (an empty
        // set means "everything in delta is shared", matching
        // AggregationManifest::is_declared()'s existing permissive
        // convention).
        const std::set<std::string> shared_names(
            config_.aggregation_manifest.shared_parameter_names.begin(),
            config_.aggregation_manifest.shared_parameter_names.end());
        const fl::core::ClippingConfig clipping_config{
            .clip_bound = clip_bound_this_round,
            .numerical_epsilon = config_.user_level_privacy.numerical_epsilon,
        };
        for (auto& update : updates) {
            if (adaptive_clip_controller_ != nullptr) {
                // Measured against the SAME bound clipping is about to
                // apply, before it's applied — never logged or exposed
                // beyond this local count (see AdaptiveClipController's
                // "no per-client norm exposure" requirement).
                const double norm = fl::core::compute_shared_norm(update.delta, shared_names);
                if (norm > clip_bound_this_round) {
                    ++over_threshold_count;
                }
            }
            update.delta = fl::core::clip_client_delta(update.delta, clipping_config, shared_names);
        }
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
    auto result = aggregator->aggregate(config_.manifest, updates, options, optimizer_state_);

    if (user_level_dp_active) {
        // Central Gaussian noise, added once to the already-aggregated
        // (clipped, weighted) result — never per-client. Sensitivity of
        // a weighted average whose weights sum to 1 is (max weight) *
        // clip_bound; this uses 1/target_clients_per_round as that
        // bound, which is exact for uniform weighting and a documented
        // approximation for capped_sample_count/normalized_bounded (see
        // docs/user-level-dp.md's "central Gaussian noise" section).
        const double effective_cohort_size =
            std::max<std::uint32_t>(config_.target_clients_per_round, 1);
        const double noise_std = config_.user_level_privacy.noise_multiplier *
                                 clip_bound_this_round / effective_cohort_size;
        result.model_delta =
            fl::core::add_central_gaussian_noise(result.model_delta, noise_std, *noise_provider_);

        user_level_accountant_->step(1);
        UserLevelLedgerEntry entry;
        entry.run_id = config_.run_id;
        entry.round_id = current_round_id_;
        entry.epsilon = user_level_accountant_->get_epsilon();
        entry.delta = config_.user_level_privacy.target_delta;
        entry.noise_multiplier = config_.user_level_privacy.noise_multiplier;
        entry.clipping_bound = clip_bound_this_round;
        entry.num_clients = static_cast<std::uint32_t>(updates.size());
        entry.committed_at_unix_s = now_unix_s;
        user_level_ledger_.push_back(std::move(entry));

        if (adaptive_clip_controller_ != nullptr) {
            // Advances the bound for NEXT round from this round's
            // (privatized) over-threshold count — separately accounted
            // from user_level_accountant_ above (Critical Privacy Rule:
            // never combine these two epsilons).
            const auto clip_result = adaptive_clip_controller_->step(
                over_threshold_count, static_cast<std::uint64_t>(updates.size()));
            AdaptiveClippingLedgerEntry clip_entry;
            clip_entry.run_id = config_.run_id;
            clip_entry.round_id = current_round_id_;
            clip_entry.epsilon = clip_result.epsilon;
            clip_entry.delta = clip_result.delta;
            clip_entry.clip_value = clip_bound_this_round;
            clip_entry.noisy_over_threshold_fraction = clip_result.noisy_over_threshold_fraction;
            adaptive_clipping_ledger_.push_back(std::move(clip_entry));
        }
    }

    for (const auto& descriptor : config_.manifest.tensors) {
        global_model_.assign(fl::core::add(global_model_.at(descriptor.name),
                                           result.model_delta.at(descriptor.name)));
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
            scaffold_global_control_.assign(
                fl::core::add(scaffold_global_control_.at(descriptor.name),
                              result.control_delta.at(descriptor.name)));
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
    emit(CoordinatorEventType::kModelVersionUpdated,
         "",
         now_unix_s,
         {{"model_version", model_version_}});

    // Privacy budget policy — reactive check (kWarnOnly/
    // kStopAfterCurrentRound/kFailRun): this round's real epsilon is now
    // known, so decide whether to warn, keep going, stop gracefully, or
    // fail. kStopBeforeExceeding was already handled preventively above
    // and never reaches this point having exceeded anything.
    bool privacy_budget_stop = false;
    bool privacy_budget_fail = false;
    const auto check_reactive_budget =
        [&](const char* mechanism, double budget, double current_epsilon) {
            if (budget <= 0.0) {
                return;  // unset: no policy applies to this mechanism
            }
            if (config_.warning_threshold_fraction > 0.0 && current_epsilon < budget &&
                current_epsilon >= budget * config_.warning_threshold_fraction) {
                emit(CoordinatorEventType::kPrivacyBudgetWarning,
                     "",
                     now_unix_s,
                     {{"mechanism", mechanism},
                      {"policy", fl::core::to_string(config_.privacy_budget_policy)}});
            }
            if (current_epsilon < budget) {
                return;
            }
            emit(CoordinatorEventType::kPrivacyBudgetExceeded,
                 "",
                 now_unix_s,
                 {{"mechanism", mechanism},
                  {"policy", fl::core::to_string(config_.privacy_budget_policy)}});
            switch (config_.privacy_budget_policy) {
                case fl::core::PrivacyBudgetPolicy::kWarnOnly:
                    break;
                case fl::core::PrivacyBudgetPolicy::kStopAfterCurrentRound:
                    privacy_budget_stop = true;
                    break;
                case fl::core::PrivacyBudgetPolicy::kFailRun:
                    privacy_budget_fail = true;
                    break;
                case fl::core::PrivacyBudgetPolicy::kStopBeforeExceeding:
                    // Defense-in-depth only: the pre-check at the top of this
                    // function should already have prevented this round from
                    // ever reaching this point over budget.
                    privacy_budget_stop = true;
                    break;
            }
        };
    if (user_level_dp_active) {
        check_reactive_budget("user_level",
                              config_.user_level_privacy.epsilon_budget,
                              user_level_accountant_->get_epsilon());
    }
    if (adaptive_clip_controller_ != nullptr) {
        check_reactive_budget("clipping",
                              config_.adaptive_clipping.epsilon_budget,
                              adaptive_clip_controller_->epsilon());
    }

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
    if (current_round_id_ >= config_.max_rounds || privacy_budget_stop || privacy_budget_fail) {
        const auto terminal_state =
            privacy_budget_fail ? fl::core::RunState::kFailed : fl::core::RunState::kCompleted;
        const std::string reason = privacy_budget_fail ? "privacy budget exceeded"
                                   : privacy_budget_stop
                                       ? "privacy budget policy stopped the run after this round"
                                       : "max_rounds reached";
        transition(terminal_state, reason, now_unix_s);
        save_checkpoint(now_unix_s);
        emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
        emit(privacy_budget_fail ? CoordinatorEventType::kRunFailed
                                 : CoordinatorEventType::kRunCompleted,
             "",
             now_unix_s);
        return;
    }
    transition(fl::core::RunState::kRunning, "round complete", now_unix_s);
    save_checkpoint(now_unix_s);
    emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
    apply_deferred_safepoint_actions(now_unix_s);
}

double RunInstance::project_user_level_epsilon_after_one_more_step() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (user_level_accountant_ == nullptr) {
        throw std::logic_error(
            "project_user_level_epsilon_after_one_more_step: this run has no user-level "
            "accountant (privacy_mode is not kUserLevelDp) -- callers must check privacy_mode() "
            "first");
    }
    return user_level_accountant_->project_epsilon(1);
}

double RunInstance::current_adaptive_clip_bound() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (adaptive_clip_controller_ == nullptr) {
        throw std::logic_error(
            "current_adaptive_clip_bound: this run has no adaptive clip controller -- callers "
            "must check secure_adaptive_clipping_active() first");
    }
    return adaptive_clip_controller_->clip_value();
}

std::uint64_t RunInstance::adaptive_clip_state_step_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (adaptive_clip_controller_ == nullptr) {
        throw std::logic_error(
            "adaptive_clip_state_step_count: this run has no adaptive clip controller -- callers "
            "must check secure_adaptive_clipping_active() first");
    }
    return adaptive_clip_controller_->steps();
}

bool RunInstance::apply_secure_aggregate_and_advance(
    std::uint64_t round_id,
    const fl::core::AggregationResult& aggregate,
    double now_unix_s,
    std::optional<std::uint64_t> indicator_over_threshold_count) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (adaptive_clip_controller_ != nullptr && !indicator_over_threshold_count.has_value()) {
        throw std::logic_error(
            "apply_secure_aggregate_and_advance: this run has adaptive clipping active but no "
            "indicator_over_threshold_count was supplied -- the caller must always reconstruct "
            "and pass the indicator count when adaptive clipping is active, never silently skip "
            "the clip-state update");
    }

    // Safe no-op, not an error: a duplicate SubmitMaskedClientUpdate
    // RPC retry (Work Area O) that arrives after this round has
    // already advanced (or targets a round that is no longer current)
    // must never re-apply -- Work Area AC's finalization-idempotency
    // requirement.
    if (round_id != current_round_id_ ||
        state_machine_.state() != fl::core::RunState::kWaitingForClients) {
        return false;
    }

    transition(fl::core::RunState::kAggregating, "secure aggregate ready", now_unix_s);

    for (const auto& descriptor : config_.manifest.tensors) {
        if (!aggregate.model_delta.contains(descriptor.name)) {
            // Defensive intersection, not an assumed exact match (see
            // this method's header comment) -- a tensor the manifest
            // lists but the secure aggregate does not cover is simply
            // left unchanged this round, not an error on its own.
            continue;
        }
        global_model_.assign(fl::core::add(global_model_.at(descriptor.name),
                                           aggregate.model_delta.at(descriptor.name)));
    }

    model_version_ = "v" + std::to_string(current_round_id_);
    config_.manifest.model_version = model_version_;

    // Secure User-Level Differential Privacy Runtime slice, Work Areas
    // M/N/R: the accountant's real, mutating step happens at exactly
    // this one call site, only after the (already noised, by the
    // caller's earlier call into SecureAggregationSessionManager::finalize())
    // aggregate has been successfully applied above -- guarded by the
    // same round-progression invariant (round_id/state_machine_) that
    // already makes this whole method idempotent on a retried RPC, so
    // the accountant step inherits that idempotency for free (see
    // docs/secure-user-level-dp-semantics.md section 12 for why this is
    // "commit," not a separate persisted reservation record). Noise
    // itself was already added inside finalize() -- this block only
    // commits the accounting and ledger record for a step that already
    // happened to the model.
    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: the bound THIS secure round actually used is the adaptive
    // controller's current (pre-step) value when adaptive clipping is
    // active for this run -- config_.user_level_privacy.initial_clipping_bound
    // was a real, latent bug for the secure path (it would have
    // recorded the wrong bound in the ledger and, before this slice,
    // was simply never reachable since AcquireTask unconditionally
    // rejected adaptive clipping under secure aggregation).
    const double model_mechanism_clip_bound =
        adaptive_clip_controller_ != nullptr ? adaptive_clip_controller_->clip_value()
                                             : config_.user_level_privacy.initial_clipping_bound;
    if ((config_.privacy_mode == fl::core::PrivacyMode::kUserLevelDp ||
         config_.privacy_mode == fl::core::PrivacyMode::kHybridDp) &&
        user_level_accountant_ != nullptr) {
        user_level_accountant_->step(1);
        UserLevelLedgerEntry entry;
        entry.run_id = config_.run_id;
        entry.round_id = current_round_id_;
        entry.epsilon = user_level_accountant_->get_epsilon();
        entry.delta = config_.user_level_privacy.target_delta;
        entry.noise_multiplier = config_.user_level_privacy.noise_multiplier;
        entry.clipping_bound = model_mechanism_clip_bound;
        entry.num_clients = static_cast<std::uint32_t>(current_cohort_.size());
        entry.committed_at_unix_s = now_unix_s;
        user_level_ledger_.push_back(std::move(entry));

        // One atomic transaction with the model mechanism's commit
        // above -- see docs/secure-adaptive-clipping-semantics.md
        // section 18. indicator_over_threshold_count is guaranteed
        // present here (checked at function entry above whenever
        // adaptive_clip_controller_ != nullptr).
        if (adaptive_clip_controller_ != nullptr) {
            const auto clip_result =
                adaptive_clip_controller_->step(*indicator_over_threshold_count,
                                                static_cast<std::uint64_t>(current_cohort_.size()));
            AdaptiveClippingLedgerEntry clip_entry;
            clip_entry.run_id = config_.run_id;
            clip_entry.round_id = current_round_id_;
            clip_entry.epsilon = clip_result.epsilon;
            clip_entry.delta = clip_result.delta;
            clip_entry.clip_value = model_mechanism_clip_bound;
            clip_entry.noisy_over_threshold_fraction = clip_result.noisy_over_threshold_fraction;
            adaptive_clipping_ledger_.push_back(std::move(clip_entry));
        }
    }
    emit(CoordinatorEventType::kAggregationCompleted, "", now_unix_s);
    emit(CoordinatorEventType::kModelVersionUpdated,
         "",
         now_unix_s,
         {{"model_version", model_version_}});

    transition(fl::core::RunState::kCheckpointing, "", now_unix_s);
    if (current_round_id_ >= config_.max_rounds) {
        transition(fl::core::RunState::kCompleted, "max_rounds reached", now_unix_s);
        save_checkpoint(now_unix_s);
        emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
        emit(CoordinatorEventType::kRunCompleted, "", now_unix_s);
        return true;
    }
    transition(fl::core::RunState::kRunning, "secure round complete", now_unix_s);
    save_checkpoint(now_unix_s);
    emit(CoordinatorEventType::kCheckpointCompleted, "", now_unix_s);
    apply_deferred_safepoint_actions(now_unix_s);
    return true;
}

void RunInstance::append_sample_level_ledger_entry(SampleLevelLedgerEntry entry) {
    std::lock_guard<std::mutex> lock(mutex_);
    sample_level_ledger_.push_back(std::move(entry));
}

std::optional<DispatchedTask> RunInstance::acquire_task(const std::string& worker_id,
                                                        double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_machine_.state() != fl::core::RunState::kWaitingForClients) {
        return std::nullopt;
    }
    // Compatible-worker-only task assignment (docs/worker-privacy-
    // capabilities.md): a worker that never advertised
    // supports_sample_level_dp at registration time must never receive a
    // task from a sample-level/hybrid-DP run — there is no silent
    // fallback to non-private training. This worker simply gets no task
    // from this run; it stays pending for whichever worker (this one,
    // after a capability upgrade + re-registration, or a different one)
    // actually advertises support.
    const bool sample_level_dp_required =
        config_.privacy_mode == fl::core::PrivacyMode::kSampleLevelDp ||
        config_.privacy_mode == fl::core::PrivacyMode::kHybridDp;
    if (sample_level_dp_required) {
        const auto worker_info = worker_registry_->get(worker_id);
        if (!worker_info.has_value() || !worker_info->capability.privacy.supports_sample_level_dp) {
            return std::nullopt;
        }
    }
    if (!dispatcher_) {
        rebuild_dispatcher_after_restore(now_unix_s);
    }
    auto task = dispatcher_->acquire(worker_id, now_unix_s);
    if (task.has_value()) {
        worker_registry_->set_current_task(worker_id, task->task_id);
        active_leases_[task->descriptor.client_id] =
            ActiveLease{worker_id, task->task_id, task->lease_id, task->lease_expires_at_unix_s};
        emit(CoordinatorEventType::kTaskAssigned,
             "",
             now_unix_s,
             {{"task_id", task->task_id}, {"client_id", task->descriptor.client_id}});
        // acquire_task does not go through transition(); persist the new
        // lease explicitly so a later, separate process can validate a
        // submission against it (see active_leases_'s doc comment).
        save_checkpoint(now_unix_s);
    }
    return task;
}

std::pair<fl::core::TensorCollection, fl::core::TensorCollection>
RunInstance::scaffold_control_variates_for(const std::string& client_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (config_.algorithm != fl::core::AggregationAlgorithm::kScaffold) {
        return {fl::core::TensorCollection{}, fl::core::TensorCollection{}};
    }
    auto global = scaffold_global_control_.empty() ? make_zero_collection(config_.manifest)
                                                   : scaffold_global_control_;

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

void RunInstance::report_task_progress(const std::string& worker_id,
                                       const std::string& task_id,
                                       const std::string& lease_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!dispatcher_) {
        throw RunManagerError("no active round to report progress against");
    }
    dispatcher_->report_progress(worker_id, task_id, lease_id);
    emit(CoordinatorEventType::kTaskProgress, "", 0.0, {{"task_id", task_id}});
}

bool RunInstance::submit_client_result(const std::string& worker_id,
                                       const std::string& task_id,
                                       const std::string& lease_id,
                                       ClientResultSubmission result,
                                       double now_unix_s,
                                       std::string& reason) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!dispatcher_) {
        rebuild_dispatcher_after_restore(now_unix_s);
    }
    const auto client_id = result.update.client_id;

    // the Algorithm Expansion phase: reject a submission carrying any tensor name the
    // aggregation manifest marks personalized-only or frozen — those
    // must never be aggregated into the global model (see
    // docs/aggregation-manifests.md). No manifest declared at all (the
    // common case for FedAvg/FedProx/SCAFFOLD) is permissive: every
    // tensor name is implicitly allowed, unchanged from pre-Algorithm-Expansion
    // behavior.
    if (config_.aggregation_manifest.is_declared()) {
        for (const auto& [tensor_name, _] : result.update.delta.tensors()) {
            const auto& personalized = config_.aggregation_manifest.personalized_parameter_names;
            const auto& frozen = config_.aggregation_manifest.frozen_parameter_names;
            const bool is_personalized =
                std::find(personalized.begin(), personalized.end(), tensor_name) !=
                personalized.end();
            const bool is_frozen =
                std::find(frozen.begin(), frozen.end(), tensor_name) != frozen.end();
            if (is_personalized || is_frozen) {
                reason = "unauthorized tensor in aggregation submission: '" + tensor_name +
                         "' is " + (is_personalized ? "personalized-only" : "frozen") +
                         " per this run's aggregation manifest";
                worker_registry_->clear_current_task(worker_id);
                worker_registry_->record_failure(worker_id);
                emit(
                    CoordinatorEventType::kClientResultRejected,
                    reason,
                    now_unix_s,
                    {{"client_id", client_id}, {"task_id", task_id}, {"tensor_name", tensor_name}});
                save_checkpoint(now_unix_s);
                return false;
            }
        }
    }

    if (round_results_.contains(client_id)) {
        reason = "duplicate result: client already has an accepted result for this round";
        worker_registry_->clear_current_task(worker_id);
        emit(CoordinatorEventType::kClientResultRejected,
             reason,
             now_unix_s,
             {{"client_id", client_id}, {"task_id", task_id}});
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
        } else if (lease.worker_id != worker_id || lease.task_id != task_id ||
                   lease.lease_id != lease_id) {
            reason = "lease mismatch: result does not belong to the current lease holder";
        } else {
            accepted = true;
        }
    } else {
        // No checkpointed lease for this client at all — the path a
        // genuinely long-lived, single-process server would always take
        // (dispatcher_ never rebuilt, so its bookkeeping is trustworthy).
        accepted = dispatcher_->submit_result(
            worker_id, task_id, lease_id, std::move(result), now_unix_s, reason);
    }

    worker_registry_->clear_current_task(worker_id);
    if (accepted) {
        round_results_[client_id] = result_copy;
        active_leases_.erase(client_id);
        if (result_copy.personalization_metrics.has_value()) {
            personalization_metrics_by_client_[client_id] = *result_copy.personalization_metrics;
        }
        if (result_copy.sample_level_privacy.has_value()) {
            // Storage/relay only — see docs/privacy-ledger.md's
            // authority-split note. Python already computed this
            // client's epsilon via Opacus; the coordinator does not
            // recompute or validate it, only appends it to this run's
            // sample-level history.
            sample_level_ledger_.push_back(*result_copy.sample_level_privacy);
        }
        emit(CoordinatorEventType::kClientResultAccepted,
             "",
             now_unix_s,
             {{"client_id", client_id}, {"task_id", task_id}});
    } else {
        worker_registry_->record_failure(worker_id);
        emit(CoordinatorEventType::kClientResultRejected,
             reason,
             now_unix_s,
             {{"client_id", client_id}, {"task_id", task_id}});
    }
    // submit_client_result does not go through transition() (the run
    // stays in WAITING_FOR_CLIENTS throughout a round), so it must
    // explicitly checkpoint round_results_ itself, or an accepted result
    // would be lost on a restart before the round finalizes.
    save_checkpoint(now_unix_s);
    return accepted;
}

bool RunInstance::cancel_lease_for_worker(const std::string& worker_id,
                                          const std::string& reason,
                                          double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!dispatcher_) {
        return false;  // no active round in this run -- nothing to cancel
    }
    const auto canceled_client_id = dispatcher_->cancel_lease_for_worker(worker_id, now_unix_s);
    if (!canceled_client_id.has_value()) {
        return false;
    }
    // Keep the checkpointed active_leases_ mirror in sync -- same erase
    // this function's accepted-result path already performs (see
    // submit_client_result above), just triggered by revocation instead
    // of a successful submission.
    active_leases_.erase(*canceled_client_id);
    worker_registry_->clear_current_task(worker_id);
    emit(CoordinatorEventType::kTaskCanceledByRevocation,
         reason,
         now_unix_s,
         {{"client_id", *canceled_client_id}, {"worker_id", worker_id}});
    save_checkpoint(now_unix_s);
    return true;
}

std::string RunInstance::checkpoint_path() const {
    return (std::filesystem::path(checkpoint_directory_) / (config_.run_id + ".checkpoint"))
        .string();
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
        body << "active_lease=" << client_id << "\t" << lease.worker_id << "\t" << lease.task_id
             << "\t" << lease.lease_id << "\t" << std::setprecision(17)
             << lease.lease_expires_at_unix_s << "\n";
    }
    body << "failed_client_count=" << failed_clients_.size() << "\n";
    for (const auto& client_id : failed_clients_) {
        body << "failed_client=" << client_id << "\n";
    }
    body << "personalization_metric_count=" << personalization_metrics_by_client_.size() << "\n";
    for (const auto& [client_id, record] : personalization_metrics_by_client_) {
        body << "personalization_metric=" << encode_personalization_metric(record) << "\n";
    }

    // Privacy Engineering phase (docs/coordinator-recovery.md): accountant
    // state is reconstructed from a single step count on restore (the
    // constructor already derives noise_multiplier/sample_rate/
    // target_delta from config_ deterministically) — only the counters
    // that actually accumulate over time need persisting. The three
    // ledgers are persisted in full so GetPrivacyLedger/GetPrivacyMetrics
    // survive a restart intact.
    if (user_level_accountant_ != nullptr) {
        body << "user_level_accountant_steps=" << user_level_accountant_->steps() << "\n";
    }
    if (adaptive_clip_controller_ != nullptr) {
        body << "adaptive_clip_value=" << std::setprecision(17)
             << adaptive_clip_controller_->clip_value() << "\n";
        body << "adaptive_clip_accountant_steps=" << adaptive_clip_controller_->steps() << "\n";
    }
    body << "sample_level_ledger_count=" << sample_level_ledger_.size() << "\n";
    for (const auto& entry : sample_level_ledger_) {
        body << "sample_level_ledger_entry=" << encode_sample_level_entry(entry) << "\n";
    }
    body << "user_level_ledger_count=" << user_level_ledger_.size() << "\n";
    for (const auto& entry : user_level_ledger_) {
        body << "user_level_ledger_entry=" << encode_user_level_entry(entry) << "\n";
    }
    body << "adaptive_clipping_ledger_count=" << adaptive_clipping_ledger_.size() << "\n";
    for (const auto& entry : adaptive_clipping_ledger_) {
        body << "adaptive_clipping_ledger_entry=" << encode_clipping_entry(entry) << "\n";
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
            throw std::runtime_error("failed to open coordinator checkpoint temp file: " +
                                     temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw std::runtime_error("failed to write coordinator checkpoint temp file: " +
                                     temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, path, error_code);
    if (error_code) {
        std::filesystem::remove(path, error_code);
        std::filesystem::rename(temp_path, path, error_code);
        if (error_code) {
            throw std::runtime_error(
                "failed to atomically move coordinator checkpoint into place: " +
                error_code.message());
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
    std::string checksum_value =
        equals == std::string::npos ? "" : checksum_line.substr(equals + 1);
    while (!checksum_value.empty() &&
           (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum_value) {
        throw std::runtime_error(
            "coordinator checkpoint checksum mismatch: file is corrupt or was truncated");
    }

    std::vector<std::pair<std::string, std::string>> fields;
    std::stringstream stream(body);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty())
            continue;
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
            active_leases_[parts[0]] =
                ActiveLease{parts[1], parts[2], parts[3], std::stod(parts[4])};
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

    personalization_metrics_by_client_.clear();
    std::size_t expected_personalization = 0;
    std::size_t found_personalization = 0;
    for (const auto& [key, value] : fields) {
        if (key == "personalization_metric_count") {
            expected_personalization = std::stoull(value);
        } else if (key == "personalization_metric") {
            auto [client_id, record] = parse_personalization_metric(value);
            personalization_metrics_by_client_[client_id] = std::move(record);
            ++found_personalization;
        }
    }
    if (found_personalization != expected_personalization) {
        throw std::runtime_error("coordinator checkpoint truncated for personalization_metric");
    }

    // Privacy Engineering phase (docs/coordinator-recovery.md):
    // user_level_accountant_/adaptive_clip_controller_ were already
    // constructed fresh (steps=0, clip_value=initial_clip) by the
    // constructor that ran before this method — see RunManager's
    // documented create_run()-then-restore_from_checkpoint() recovery
    // sequence. Catch them up from the checkpointed step count/clip
    // value; a checkpoint saved before either was constructed (e.g. a
    // non-private run) simply has no matching field, so this is a no-op
    // for those runs.
    for (const auto& [key, value] : fields) {
        if (key == "user_level_accountant_steps" && user_level_accountant_ != nullptr) {
            user_level_accountant_->step(std::stoull(value));
        }
    }
    if (adaptive_clip_controller_ != nullptr) {
        std::optional<double> restored_clip_value;
        std::optional<std::uint64_t> restored_steps;
        for (const auto& [key, value] : fields) {
            if (key == "adaptive_clip_value") {
                restored_clip_value = std::stod(value);
            } else if (key == "adaptive_clip_accountant_steps") {
                restored_steps = std::stoull(value);
            }
        }
        if (restored_clip_value.has_value() && restored_steps.has_value()) {
            adaptive_clip_controller_->restore(*restored_clip_value, *restored_steps);
        }
    }

    sample_level_ledger_.clear();
    std::size_t expected_sample_entries = 0;
    std::size_t found_sample_entries = 0;
    for (const auto& [key, value] : fields) {
        if (key == "sample_level_ledger_count") {
            expected_sample_entries = std::stoull(value);
        } else if (key == "sample_level_ledger_entry") {
            sample_level_ledger_.push_back(parse_sample_level_entry(value));
            ++found_sample_entries;
        }
    }
    if (found_sample_entries != expected_sample_entries) {
        throw std::runtime_error("coordinator checkpoint truncated for sample_level_ledger_entry");
    }

    user_level_ledger_.clear();
    std::size_t expected_user_entries = 0;
    std::size_t found_user_entries = 0;
    for (const auto& [key, value] : fields) {
        if (key == "user_level_ledger_count") {
            expected_user_entries = std::stoull(value);
        } else if (key == "user_level_ledger_entry") {
            user_level_ledger_.push_back(parse_user_level_entry(value));
            ++found_user_entries;
        }
    }
    if (found_user_entries != expected_user_entries) {
        throw std::runtime_error("coordinator checkpoint truncated for user_level_ledger_entry");
    }

    adaptive_clipping_ledger_.clear();
    std::size_t expected_clipping_entries = 0;
    std::size_t found_clipping_entries = 0;
    for (const auto& [key, value] : fields) {
        if (key == "adaptive_clipping_ledger_count") {
            expected_clipping_entries = std::stoull(value);
        } else if (key == "adaptive_clipping_ledger_entry") {
            adaptive_clipping_ledger_.push_back(parse_clipping_entry(value));
            ++found_clipping_entries;
        }
    }
    if (found_clipping_entries != expected_clipping_entries) {
        throw std::runtime_error(
            "coordinator checkpoint truncated for adaptive_clipping_ledger_entry");
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

RunManager::RunManager(CoordinatorConfig config,
                       std::string checkpoint_root_directory,
                       std::string scaffold_state_root_directory)
    : config_(config),
      checkpoint_root_directory_(std::move(checkpoint_root_directory)),
      scaffold_state_root_directory_(std::move(scaffold_state_root_directory)),
      worker_registry_(config.missed_heartbeat_threshold,
                       config.default_heartbeat_interval_seconds),
      event_bus_(config.event_bus_capacity_per_run),
      scaffold_store_(
          std::make_unique<FilesystemClientAlgorithmStateStore>(scaffold_state_root_directory_)) {}

std::string RunManager::create_run(RunConfig config, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (runs_.contains(config.run_id)) {
        throw RunManagerError("duplicate run_id: " + config.run_id);
    }
    if (runs_.size() >= config_.max_concurrent_runs) {
        throw RunManagerError("maximum concurrent run limit reached: " +
                              std::to_string(config_.max_concurrent_runs));
    }
    const auto run_id = config.run_id;
    auto instance = std::make_unique<RunInstance>(config,
                                                  config_,
                                                  event_bus_,
                                                  worker_registry_,
                                                  scaffold_store_.get(),
                                                  checkpoint_root_directory_);
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

std::uint32_t RunManager::cancel_leases_for_worker(const std::string& worker_id,
                                                   const std::string& reason,
                                                   double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::uint32_t canceled_count = 0;
    for (const auto& [run_id, instance] : runs_) {
        if (instance->cancel_lease_for_worker(worker_id, reason, now_unix_s)) {
            ++canceled_count;
        }
    }
    return canceled_count;
}

}  // namespace fl::coordinator
