from __future__ import annotations

import pytest

from fl_platform.execution import (
    ExecutionMode,
    MultiprocessingOrchestrator,
    SchedulingConfig,
)
from fl_platform.v3 import AggregationConfig, V3AggregationEngine
from fl_platform.v3.server_optimizers import OptimizerConfig
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class UpdateTrainer:
    def train(self, task: TrainingTask) -> TrainingResult:
        base_version = int(task.training_config.get("base_version", 0))
        update = tuple(task.training_config.get("model_update", (1.0, 1.0)))
        sample_count = int(task.training_config.get("sample_count", 1))
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=sample_count,
            local_step_count=1,
            base_model_version=base_version,
            model_update=update,
        )


def make_result(
    client_id: str,
    update: tuple[float, ...] | None,
    *,
    samples: int = 1,
    base_version: int = 0,
) -> TrainingResult:
    return TrainingResult(
        run_id="run-v3",
        round_id=1,
        client_id=client_id,
        model_version=f"model-v{base_version}",
        sample_count=samples,
        local_step_count=1,
        base_model_version=base_version,
        model_update=update,
    )


def make_task(
    client_id: str,
    base_version: int,
    update: tuple[float, float],
) -> TrainingTask:
    return TrainingTask(
        run_id="run-v3",
        round_id=1,
        client_id=client_id,
        model_version=f"model-v{base_version}",
        algorithm="fedavg",
        training_config={
            "base_version": base_version,
            "model_update": update,
        },
    )


def test_sample_weighted_mean_uses_client_sample_counts() -> None:
    engine = V3AggregationEngine(
        2,
        AggregationConfig(strategy="mean", weighting="sample_count"),
    )
    outcome = engine.aggregate(
        [
            make_result("small", (0.0, 0.0), samples=1),
            make_result("large", (10.0, 20.0), samples=9),
        ]
    )
    assert outcome.update == pytest.approx((9.0, 18.0))
    assert outcome.total_samples == 10


def test_coordinate_median_rejects_extreme_byzantine_influence() -> None:
    engine = V3AggregationEngine(
        2,
        AggregationConfig(strategy="median", weighting="uniform"),
    )
    outcome = engine.aggregate(
        [
            make_result("a", (1.0, 1.0)),
            make_result("b", (1.1, 0.9)),
            make_result("c", (0.9, 1.1)),
            make_result("malicious", (1000.0, -1000.0)),
            make_result("d", (1.0, 1.0)),
        ]
    )
    assert outcome.update == pytest.approx((1.0, 1.0))


def test_robust_aggregation_fails_closed_with_sample_weighting() -> None:
    with pytest.raises(ValueError, match="requires uniform weighting"):
        V3AggregationEngine(
            2,
            AggregationConfig(strategy="trimmed_mean", weighting="sample_count"),
        )


def test_aggregation_rejects_missing_or_non_finite_updates() -> None:
    engine = V3AggregationEngine(2, AggregationConfig(weighting="uniform"))
    with pytest.raises(ValueError, match="did not provide"):
        engine.aggregate([make_result("missing", None)])
    with pytest.raises(ValueError, match="non-finite"):
        engine.aggregate([make_result("bad", (float("nan"), 0.0))])


def test_adaptive_optimizer_is_stateful_across_aggregations() -> None:
    engine = V3AggregationEngine(
        2,
        AggregationConfig(
            strategy="mean",
            weighting="uniform",
            optimizer=OptimizerConfig(name="fedadam", learning_rate=0.1),
        ),
    )
    first = engine.aggregate([make_result("a", (1.0, -1.0))])
    second = engine.aggregate([make_result("a", (0.5, -0.5))])
    assert first.optimizer == "fedadam"
    assert second.optimizer == "fedadam"
    assert first.update != second.update


def test_orchestrator_uses_real_model_version_staleness_and_aggregation() -> None:
    engine = V3AggregationEngine(
        2,
        AggregationConfig(strategy="mean", weighting="uniform"),
    )
    orchestrator = MultiprocessingOrchestrator(
        WorkerService(UpdateTrainer()),
        SchedulingConfig(
            mode=ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS,
            target_clients=2,
            minimum_clients=1,
            buffer_size=2,
            maximum_staleness=1,
            current_model_version=5,
        ),
        aggregator=engine,
    )
    result = orchestrator.run(
        [
            make_task("fresh", 5, (1.0, 1.0)),
            make_task("allowed-stale", 4, (3.0, 3.0)),
            make_task("too-stale", 3, (100.0, 100.0)),
            make_task("future", 6, (100.0, 100.0)),
        ]
    )
    assert [item.client_id for item in result.accepted] == [
        "fresh",
        "allowed-stale",
    ]
    assert [item.client_id for item in result.rejected] == ["too-stale", "future"]
    assert result.deferred == []
    assert result.aggregation is not None
    assert result.aggregation.update == pytest.approx((2.0, 2.0))


def test_explicit_staleness_mode_defers_fresh_overflow() -> None:
    orchestrator = MultiprocessingOrchestrator(
        WorkerService(UpdateTrainer()),
        SchedulingConfig(
            mode=ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS,
            target_clients=1,
            minimum_clients=1,
            buffer_size=1,
            maximum_staleness=2,
            current_model_version=5,
        ),
    )
    result = orchestrator.run(
        [
            make_task("one", 5, (1.0, 1.0)),
            make_task("two", 4, (2.0, 2.0)),
        ]
    )
    assert [item.client_id for item in result.accepted] == ["one"]
    assert [item.client_id for item in result.deferred] == ["two"]
    assert result.rejected == []
