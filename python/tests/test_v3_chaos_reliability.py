from __future__ import annotations

import pytest

from fl_platform.v3.chaos_reliability import (
    ChaosFault,
    ChaosProfile,
    ChaosRoundExecutor,
    DeterministicChaosPlan,
)
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class StableTrainer:
    def train(self, task: TrainingTask) -> TrainingResult:
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=8,
            local_step_count=2,
            model_update=(1.0, -1.0),
        )


def _tasks(count: int = 8) -> tuple[TrainingTask, ...]:
    return tuple(
        TrainingTask(
            run_id="run-chaos",
            round_id=3,
            client_id=f"client-{index}",
            model_version="model-v4",
            algorithm="fedavg",
        )
        for index in range(count)
    )


def _profile_for(fault: ChaosFault) -> ChaosProfile:
    return ChaosProfile(
        drop_probability=1.0 if fault == ChaosFault.DROP_BEFORE_TRAIN else 0.0,
        transient_crash_probability=(
            1.0 if fault == ChaosFault.TRANSIENT_CRASH else 0.0
        ),
        permanent_crash_probability=(
            1.0 if fault == ChaosFault.PERMANENT_CRASH else 0.0
        ),
        delay_probability=1.0 if fault == ChaosFault.RESULT_DELAY else 0.0,
        duplicate_replay_probability=(
            1.0 if fault == ChaosFault.DUPLICATE_REPLAY else 0.0
        ),
    )


def test_fault_plan_is_deterministic_for_same_seed_and_task_identity() -> None:
    profile = ChaosProfile(
        drop_probability=0.2,
        transient_crash_probability=0.2,
        permanent_crash_probability=0.1,
        delay_probability=0.2,
        duplicate_replay_probability=0.2,
    )
    first = DeterministicChaosPlan(seed=17, profile=profile)
    second = DeterministicChaosPlan(seed=17, profile=profile)
    assert [first.decide(task) for task in _tasks()] == [
        second.decide(task) for task in _tasks()
    ]


def test_drop_fault_never_calls_worker_and_accounts_all_clients() -> None:
    executor = ChaosRoundExecutor(
        WorkerService(StableTrainer()),
        DeterministicChaosPlan(
            seed=1,
            profile=_profile_for(ChaosFault.DROP_BEFORE_TRAIN),
        ),
    )
    result = executor.run(_tasks(4))
    assert not result.results
    assert len(result.dropped_clients) == 4
    assert not result.failed_clients
    assert result.recovery_rate == 0.0


def test_transient_crash_recovers_with_bounded_retry_budget() -> None:
    executor = ChaosRoundExecutor(
        WorkerService(StableTrainer()),
        DeterministicChaosPlan(
            seed=2,
            profile=_profile_for(ChaosFault.TRANSIENT_CRASH),
        ),
        max_retries=1,
    )
    result = executor.run(_tasks(5))
    assert len(result.results) == 5
    assert result.retry_attempts == 5
    assert not result.failed_clients
    assert result.recovery_rate == 1.0


def test_transient_crash_fails_closed_without_retry_budget() -> None:
    executor = ChaosRoundExecutor(
        WorkerService(StableTrainer()),
        DeterministicChaosPlan(
            seed=3,
            profile=_profile_for(ChaosFault.TRANSIENT_CRASH),
        ),
        max_retries=0,
    )
    result = executor.run(_tasks(3))
    assert not result.results
    assert len(result.failed_clients) == 3


def test_permanent_crash_exhausts_retry_budget_without_result() -> None:
    executor = ChaosRoundExecutor(
        WorkerService(StableTrainer()),
        DeterministicChaosPlan(
            seed=4,
            profile=_profile_for(ChaosFault.PERMANENT_CRASH),
        ),
        max_retries=2,
    )
    result = executor.run(_tasks(3))
    assert not result.results
    assert len(result.failed_clients) == 3
    assert result.retry_attempts == 6


def test_delayed_results_are_recovered_after_on_time_window() -> None:
    executor = ChaosRoundExecutor(
        WorkerService(StableTrainer()),
        DeterministicChaosPlan(
            seed=5,
            profile=_profile_for(ChaosFault.RESULT_DELAY),
        ),
    )
    result = executor.run(_tasks(4))
    assert len(result.results) == 4
    assert result.delayed_clients == tuple(task.client_id for task in _tasks(4))
    assert result.recovery_rate == 1.0


def test_duplicate_replay_is_counted_but_never_accepted_twice() -> None:
    executor = ChaosRoundExecutor(
        WorkerService(StableTrainer()),
        DeterministicChaosPlan(
            seed=6,
            profile=_profile_for(ChaosFault.DUPLICATE_REPLAY),
        ),
    )
    result = executor.run(_tasks(6))
    clients = tuple(item.client_id for item in result.results)
    assert len(clients) == len(set(clients)) == 6
    assert result.replay_rejections == 6


def test_seeded_soak_preserves_accounting_and_uniqueness_invariants() -> None:
    profile = ChaosProfile(
        drop_probability=0.12,
        transient_crash_probability=0.12,
        permanent_crash_probability=0.06,
        delay_probability=0.15,
        duplicate_replay_probability=0.15,
    )
    tasks = _tasks(32)
    for seed in range(100):
        result = ChaosRoundExecutor(
            WorkerService(StableTrainer()),
            DeterministicChaosPlan(seed=seed, profile=profile),
            max_retries=1,
        ).run(tasks)
        result.validate_invariants()
        assert len({item.client_id for item in result.results}) == len(result.results)
        assert 0.0 <= result.recovery_rate <= 1.0


def test_invalid_probability_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="sum"):
        DeterministicChaosPlan(
            seed=1,
            profile=ChaosProfile(
                drop_probability=0.5,
                transient_crash_probability=0.5,
                permanent_crash_probability=0.5,
            ),
        )
