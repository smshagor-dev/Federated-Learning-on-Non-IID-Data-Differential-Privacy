from __future__ import annotations

import json
from pathlib import Path

from fl_platform.v3.heterogeneous_execution import HeterogeneityRoundMetrics
from fl_platform.v3.observability import RobustnessMetrics, RoundMetrics
from fl_platform.v3.observability_runtime import (
    JsonlMetricSink,
    MetricEvent,
    V3MetricRegistry,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[MetricEvent] = []

    def record(self, event: MetricEvent) -> None:
        self.events.append(event)


def _registry() -> V3MetricRegistry:
    registry = V3MetricRegistry()
    registry.record_round(
        RoundMetrics(
            round_id=7,
            cohort_size=10,
            accepted_updates=8,
            dropped_clients=2,
            round_latency_seconds=4.5,
            aggregation_seconds=0.25,
            upload_bytes=1000,
            download_bytes=2000,
            privacy_epsilon=1.75,
        )
    )
    registry.record_robustness(
        RobustnessMetrics(
            attack_name='backdoor"probe',
            malicious_clients=2,
            attack_success_rate=0.1,
            clean_accuracy=0.92,
            attacked_accuracy=0.89,
        )
    )
    registry.record_heterogeneity(
        HeterogeneityRoundMetrics(
            selected_clients=10,
            admitted_clients=8,
            unavailable_clients=1,
            resource_ineligible_clients=0,
            deadline_miss_clients=1,
            mean_estimated_round_seconds=2.2,
            max_estimated_round_seconds=4.0,
            estimated_communication_bytes=24000,
        )
    )
    return registry


def test_prometheus_text_contains_aggregate_metrics() -> None:
    text = _registry().prometheus_text()
    assert "fl_round_accepted_updates 8" in text
    assert "fl_round_communication_bytes 3000" in text
    assert "fl_round_privacy_epsilon 1.75" in text
    assert "fl_heterogeneity_deadline_miss_clients 1" in text
    assert 'attack="backdoor\\"probe"' in text
    assert "fl_robustness_attack_success_rate" in text


def test_registry_has_no_client_identity_or_raw_update_fields() -> None:
    events = _registry().events()
    names = {event.name for event in events}
    forbidden_fragments = (
        "client_id",
        "worker_id",
        "gradient",
        "model_update",
        "sample_value",
    )
    assert all(
        fragment not in name for name in names for fragment in forbidden_fragments
    )
    assert all(set(event.attributes) <= {"attack"} for event in events)


def test_generic_sink_receives_same_deterministic_metric_events() -> None:
    registry = _registry()
    sink = RecordingSink()
    registry.export_to(sink)
    assert tuple(sink.events) == registry.events()
    assert tuple(event.name for event in sink.events) == tuple(
        sorted(event.name for event in sink.events)
    )


def test_jsonl_sink_writes_machine_readable_aggregate_records(
    tmp_path: Path,
) -> None:
    registry = _registry()
    path = tmp_path / "metrics.jsonl"
    registry.export_to(JsonlMetricSink(path))
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(records) == len(registry.events())
    assert all(set(record) == {"attributes", "name", "value"} for record in records)
    forbidden_fragments = (
        "client_id",
        "worker_id",
        "gradient",
        "model_update",
        "sample_value",
    )
    assert all(
        fragment not in json.dumps(record, sort_keys=True).lower()
        for record in records
        for fragment in forbidden_fragments
    )


def test_recording_new_round_replaces_gauges_and_removes_stale_epsilon() -> None:
    registry = _registry()
    registry.record_round(
        RoundMetrics(
            round_id=8,
            cohort_size=6,
            accepted_updates=6,
            dropped_clients=0,
            round_latency_seconds=1.0,
            aggregation_seconds=0.1,
            upload_bytes=10,
            download_bytes=20,
        )
    )
    values = {
        event.name: event.value for event in registry.events() if not event.attributes
    }
    assert values["fl_round_id"] == 8
    assert values["fl_round_cohort_size"] == 6
    assert values["fl_round_communication_bytes"] == 30
    assert "fl_round_privacy_epsilon" not in values
