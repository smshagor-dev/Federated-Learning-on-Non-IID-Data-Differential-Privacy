#include "fl_coordinator/event_bus.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

void run_event_bus_tests() {
    using fl::coordinator::CoordinatorEvent;
    using fl::coordinator::CoordinatorEventType;
    using fl::coordinator::EventBus;

    {
        EventBus bus(/*capacity_per_run=*/1000);
        CoordinatorEvent a;
        a.run_id = "run-1";
        a.type = CoordinatorEventType::kRunCreated;
        const auto published_a = bus.publish(a, "2026-01-01T00:00:00Z");

        CoordinatorEvent b;
        b.run_id = "run-1";
        b.type = CoordinatorEventType::kRunStarted;
        const auto published_b = bus.publish(b, "2026-01-01T00:00:01Z");

        check(published_a.event_id != published_b.event_id, "each published event gets a distinct event_id");

        const auto all = bus.poll("run-1", "");
        check(all.size() == 2, "polling from the start returns every published event");
        check(all[0].type == CoordinatorEventType::kRunCreated, "events are returned in publish order (first)");
        check(all[1].type == CoordinatorEventType::kRunStarted, "events are returned in publish order (second)");

        const auto after_first = bus.poll("run-1", published_a.event_id);
        check(after_first.size() == 1 && after_first[0].type == CoordinatorEventType::kRunStarted,
              "polling after a cursor only returns subsequent events");
    }

    {
        // Events for different runs must not interleave or leak into
        // each other's history.
        EventBus bus(1000);
        CoordinatorEvent for_run_1;
        for_run_1.run_id = "run-1";
        bus.publish(for_run_1, "t");
        CoordinatorEvent for_run_2;
        for_run_2.run_id = "run-2";
        bus.publish(for_run_2, "t");

        check(bus.poll("run-1", "").size() == 1, "run-1's history excludes run-2's events");
        check(bus.poll("run-2", "").size() == 1, "run-2's history excludes run-1's events");
    }

    {
        // Bounded per-subscriber history: the slow-subscriber policy
        // drops the oldest events once capacity is exceeded.
        EventBus bus(/*capacity_per_run=*/3);
        for (int i = 0; i < 10; ++i) {
            CoordinatorEvent event;
            event.run_id = "run-1";
            bus.publish(event, "t");
        }
        check(bus.history_size("run-1") == 3, "history never grows past capacity_per_run");
        const auto remaining = bus.poll("run-1", "");
        check(remaining.size() == 3, "poll from the beginning only returns what's still retained");
    }
}

}  // namespace fl::coordinator::testing
