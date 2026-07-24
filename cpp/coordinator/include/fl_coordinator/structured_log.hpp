#pragma once

#include "fl_coordinator/event_bus.hpp"

namespace fl::coordinator {

// Writes one structured key=value line per coordinator event to stderr
// (unbuffered by default, unlike std::cout — see docs/coordinator-recovery.md
// for how buffered stdout previously made the coordinator server look
// silent/hung under `docker logs` even while healthy). This is
// deliberately not a full logging framework: one call site
// (RunInstance::emit, run_manager.cpp) covers every run/round/task/result
// lifecycle transition, since every one of them already goes through
// emit() to publish a CoordinatorEvent to the event bus.
void log_event(const CoordinatorEvent& event, const char* service = "coordinator");

}  // namespace fl::coordinator
