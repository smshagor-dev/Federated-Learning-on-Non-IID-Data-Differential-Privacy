import unittest

from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class DummyTrainer:
    def train(self, task: TrainingTask) -> TrainingResult:
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=12,
            local_step_count=4,
            metrics={"loss": 1.5},
            trace_id=task.trace_id,
        )


class WorkerServiceTests(unittest.TestCase):
    def test_worker_service_stamps_worker_id(self) -> None:
        service = WorkerService(DummyTrainer(), worker_id="worker-a")
        result = service.handle_task(
            TrainingTask(
                run_id="run-1",
                round_id=3,
                client_id="client-9",
                model_version="model-v1",
                algorithm="fedavg",
                trace_id="trace-123",
            )
        )
        self.assertEqual(result.worker_id, "worker-a")
        self.assertEqual(result.trace_id, "trace-123")
        self.assertEqual(result.sample_count, 12)
