#include "fl_coordinator/structured_log.hpp"

#include <iostream>

namespace fl::coordinator {

namespace {

void write_field(std::ostream& out, const char* key, const std::string& value) {
    if (value.empty()) {
        return;
    }
    out << ' ' << key << '=' << value;
}

}  // namespace

void log_event(const CoordinatorEvent& event, const char* service) {
    std::cerr << "timestamp=" << event.timestamp << " service=" << service
               << " event_type=" << to_string(event.type);
    write_field(std::cerr, "run_id", event.run_id);
    if (event.round_id != 0) {
        std::cerr << " round_id=" << event.round_id;
    }
    write_field(std::cerr, "client_id", event.client_id);
    write_field(std::cerr, "worker_id", event.worker_id);
    write_field(std::cerr, "model_version", event.model_version);
    write_field(std::cerr, "trace_id", event.trace_id);
    write_field(std::cerr, "event_id", event.event_id);
    write_field(std::cerr, "reason", event.reason);
    for (const auto& [key, value] : event.metadata) {
        write_field(std::cerr, key.c_str(), value);
    }
    std::cerr << '\n' << std::flush;
}

}  // namespace fl::coordinator
