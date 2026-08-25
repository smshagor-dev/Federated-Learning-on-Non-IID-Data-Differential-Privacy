"""Privacy-safe runtime exporters for v3 operational metrics.

Only aggregate round/robustness/heterogeneity measurements are exported. The
registry has no API for model vectors, raw gradients, examples, or client IDs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from fl_platform.v3.heterogeneous_execution import HeterogeneityRoundMetrics
from fl_platform.v3.observability import RobustnessMetrics, RoundMetrics


@dataclass(frozen=True)
class MetricEvent:
    name: str
    value: float
    attributes: Mapping[str, str]


class MetricEventSink(Protocol):
    """Adapter boundary for OpenTelemetry or another metric backend."""

    def record(self, event: MetricEvent) -> None: ...


def _validate_metric_value(value: float, *, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"metric {name} must be finite")
    return numeric


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class V3MetricRegistry:
    """In-memory aggregate registry with Prometheus and event-sink export."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def _set(
        self,
        name: str,
        value: int | float,
        *,
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        labels = tuple(sorted((attributes or {}).items()))
        self._values[(name, labels)] = _validate_metric_value(float(value), name=name)

    def record_round(self, metrics: RoundMetrics) -> None:
        metrics.validate()
        self._set("fl_round_id", metrics.round_id)
        self._set("fl_round_cohort_size", metrics.cohort_size)
        self._set("fl_round_accepted_updates", metrics.accepted_updates)
        self._set("fl_round_dropped_clients", metrics.dropped_clients)
        self._set("fl_round_latency_seconds", metrics.round_latency_seconds)
        self._set("fl_round_aggregation_seconds", metrics.aggregation_seconds)
        self._set("fl_round_upload_bytes", metrics.upload_bytes)
        self._set("fl_round_download_bytes", metrics.download_bytes)
        self._set("fl_round_communication_bytes", metrics.communication_bytes)
        if metrics.privacy_epsilon is not None:
            self._set("fl_round_privacy_epsilon", metrics.privacy_epsilon)

    def record_robustness(self, metrics: RobustnessMetrics) -> None:
        metrics.validate()
        attributes = {"attack": metrics.attack_name}
        self._set(
            "fl_robustness_malicious_clients",
            metrics.malicious_clients,
            attributes=attributes,
        )
        self._set(
            "fl_robustness_attack_success_rate",
            metrics.attack_success_rate,
            attributes=attributes,
        )
        self._set(
            "fl_robustness_clean_accuracy",
            metrics.clean_accuracy,
            attributes=attributes,
        )
        self._set(
            "fl_robustness_attacked_accuracy",
            metrics.attacked_accuracy,
            attributes=attributes,
        )
        self._set(
            "fl_robustness_accuracy_degradation",
            metrics.accuracy_degradation,
            attributes=attributes,
        )

    def record_heterogeneity(self, metrics: HeterogeneityRoundMetrics) -> None:
        self._set("fl_heterogeneity_selected_clients", metrics.selected_clients)
        self._set("fl_heterogeneity_admitted_clients", metrics.admitted_clients)
        self._set(
            "fl_heterogeneity_unavailable_clients",
            metrics.unavailable_clients,
        )
        self._set(
            "fl_heterogeneity_resource_ineligible_clients",
            metrics.resource_ineligible_clients,
        )
        self._set(
            "fl_heterogeneity_deadline_miss_clients",
            metrics.deadline_miss_clients,
        )
        self._set(
            "fl_heterogeneity_mean_estimated_round_seconds",
            metrics.mean_estimated_round_seconds,
        )
        self._set(
            "fl_heterogeneity_max_estimated_round_seconds",
            metrics.max_estimated_round_seconds,
        )
        self._set(
            "fl_heterogeneity_estimated_communication_bytes",
            metrics.estimated_communication_bytes,
        )

    def events(self) -> tuple[MetricEvent, ...]:
        return tuple(
            MetricEvent(name=name, value=value, attributes=dict(labels))
            for (name, labels), value in sorted(self._values.items())
        )

    def export_to(self, sink: MetricEventSink) -> None:
        for event in self.events():
            sink.record(event)

    def prometheus_text(self) -> str:
        lines: list[str] = []
        for event in self.events():
            label_text = ""
            if event.attributes:
                rendered = ",".join(
                    f'{name}="{_escape_label(value)}"'
                    for name, value in sorted(event.attributes.items())
                )
                label_text = "{" + rendered + "}"
            lines.append(f"# TYPE {event.name} gauge")
            lines.append(f"{event.name}{label_text} {event.value:.17g}")
        return "\n".join(lines) + ("\n" if lines else "")


class JsonlMetricSink:
    """Append aggregate metric events as deterministic JSON Lines records."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def record(self, event: MetricEvent) -> None:
        payload = asdict(event)
        payload["attributes"] = dict(sorted(event.attributes.items()))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


__all__ = [
    "JsonlMetricSink",
    "MetricEvent",
    "MetricEventSink",
    "V3MetricRegistry",
]
