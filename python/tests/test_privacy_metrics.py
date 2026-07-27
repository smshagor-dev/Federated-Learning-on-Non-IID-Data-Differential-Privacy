"""Regression coverage for the worker's sample-level-DP Prometheus
metrics (docs/privacy-mathematics.md's Critical Privacy Rule): the
gauge must always be labeled mechanism="sample_level" and must never be
set to a fabricated value — only a real epsilon reported by
run_private_local_training's SampleLevelPrivacyResult.
"""

from __future__ import annotations

import unittest

from fl_platform.privacy.metrics import (
    SAMPLE_LEVEL_EPSILON,
    SAMPLE_LEVEL_TRAINING_ROUNDS_TOTAL,
    ensure_metrics_server_started,
    record_sample_level_training_rejected,
    record_sample_level_training_success,
)


def _rounds_total(outcome: str) -> float:
    return SAMPLE_LEVEL_TRAINING_ROUNDS_TOTAL.labels(outcome=outcome)._value.get()


class SampleLevelMetricsTests(unittest.TestCase):
    def test_success_sets_the_labeled_epsilon_gauge(self) -> None:
        record_sample_level_training_success("run-1", "client-a", 1.75)
        value = SAMPLE_LEVEL_EPSILON.labels(
            mechanism="sample_level", run_id="run-1", client_id="client-a"
        )._value.get()
        self.assertAlmostEqual(value, 1.75)

    def test_success_increments_the_success_counter(self) -> None:
        before = _rounds_total("success")
        record_sample_level_training_success("run-1", "client-b", 2.0)
        self.assertEqual(_rounds_total("success"), before + 1)

    def test_rejected_increments_the_rejected_counter_not_success(self) -> None:
        before = _rounds_total("rejected")
        record_sample_level_training_rejected()
        self.assertEqual(_rounds_total("rejected"), before + 1)

    def test_epsilon_gauge_is_updated_in_place_not_accumulated(self) -> None:
        record_sample_level_training_success("run-2", "client-a", 1.0)
        record_sample_level_training_success("run-2", "client-a", 3.0)
        value = SAMPLE_LEVEL_EPSILON.labels(
            mechanism="sample_level", run_id="run-2", client_id="client-a"
        )._value.get()
        # A gauge, not a counter: the second call replaces the first
        # value (3.0), it never sums to 4.0.
        self.assertAlmostEqual(value, 3.0)


class MetricsServerTests(unittest.TestCase):
    def test_port_zero_never_binds_a_server(self) -> None:
        # Must not raise (e.g. OSError from a real bind attempt) — 0 is
        # the documented "disabled" sentinel, not a request for an
        # OS-assigned ephemeral port.
        ensure_metrics_server_started(0)


if __name__ == "__main__":
    unittest.main()
