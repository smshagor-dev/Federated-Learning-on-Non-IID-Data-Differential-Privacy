import unittest

from fl_platform.execution import (
    ExecutionMode,
    MultiprocessingOrchestrator,
    SchedulingConfig,
    validate_scheduling_config,
)
from fl_platform.flower import FlowerSimulationAdapter
from fl_platform.ray import RayWorkerAdapter
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class DummyTrainer:
    def train(self, task: TrainingTask) -> TrainingResult:
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=5,
            local_step_count=2,
            metrics={"loss": float(task.round_id)},
            trace_id=task.trace_id,
        )


def make_tasks(count: int) -> list[TrainingTask]:
    return [
        TrainingTask(
            run_id="run-1",
            round_id=index + 1,
            client_id=f"client-{index}",
            model_version="model-v1",
            algorithm="fedavg",
            trace_id=f"trace-{index}",
        )
        for index in range(count)
    ]


class ExecutionFoundationTests(unittest.TestCase):
    def test_validate_synchronous_config(self) -> None:
        result = validate_scheduling_config(
            SchedulingConfig(
                mode=ExecutionMode.SYNCHRONOUS, target_clients=4, minimum_clients=4
            )
        )
        self.assertTrue(result.valid)

    def test_validate_semisync_requires_deadline(self) -> None:
        result = validate_scheduling_config(
            SchedulingConfig(
                mode=ExecutionMode.DEADLINE_BASED_SEMI_SYNCHRONOUS,
                target_clients=4,
                minimum_clients=2,
            )
        )
        self.assertFalse(result.valid)

    def test_orchestrator_buffered_async_defers_extra_results(self) -> None:
        orchestrator = MultiprocessingOrchestrator(
            WorkerService(DummyTrainer(), worker_id="worker-x"),
            SchedulingConfig(
                mode=ExecutionMode.BUFFERED_ASYNCHRONOUS,
                target_clients=2,
                minimum_clients=1,
                buffer_size=2,
            ),
        )
        result = orchestrator.run(make_tasks(4))
        self.assertEqual(len(result.accepted), 2)
        self.assertEqual(len(result.deferred), 2)

    def test_orchestrator_staleness_mode_rejects_when_zero_staleness(self) -> None:
        orchestrator = MultiprocessingOrchestrator(
            WorkerService(DummyTrainer(), worker_id="worker-y"),
            SchedulingConfig(
                mode=ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS,
                target_clients=2,
                minimum_clients=1,
                buffer_size=2,
                maximum_staleness=0,
            ),
        )
        result = orchestrator.run(make_tasks(3))
        self.assertEqual(len(result.accepted), 2)
        self.assertEqual(len(result.rejected), 1)

    def test_ray_adapter_builds_plan(self) -> None:
        adapter = RayWorkerAdapter()
        plan = adapter.build_plan(
            make_tasks(5),
            SchedulingConfig(
                mode=ExecutionMode.SYNCHRONOUS, target_clients=3, minimum_clients=3
            ),
            num_workers=2,
        )
        self.assertEqual(plan.num_workers, 2)
        self.assertEqual(plan.task_count, 5)

    def test_flower_adapter_builds_plan(self) -> None:
        adapter = FlowerSimulationAdapter()
        plan = adapter.build_plan(
            10,
            SchedulingConfig(
                mode=ExecutionMode.SYNCHRONOUS, target_clients=4, minimum_clients=2
            ),
        )
        self.assertAlmostEqual(plan.fraction_fit, 0.4)
        self.assertEqual(plan.min_available_clients, 2)


if __name__ == "__main__":
    unittest.main()
