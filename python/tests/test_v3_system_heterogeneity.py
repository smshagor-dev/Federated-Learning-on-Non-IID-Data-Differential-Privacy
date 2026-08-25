from __future__ import annotations

from fl_platform.execution import ExecutionMode, MultiprocessingOrchestrator, SchedulingConfig
from fl_platform.v3.heterogeneity import ClientSystemProfile, EdgeRequirements
from fl_platform.v3.heterogeneous_execution import HeterogeneityAdmissionPolicy
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class RecordingTrainer:
    def __init__(self) -> None:
        self.clients: list[str] = []

    def train(self, task: TrainingTask) -> TrainingResult:
        self.clients.append(task.client_id)
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=10,
            local_step_count=2,
        )


def _task(client_id: str, *, round_id: int = 3) -> TrainingTask:
    return TrainingTask(
        run_id="run-heterogeneous",
        round_id=round_id,
        client_id=client_id,
        model_version="model-v1",
        algorithm="fedavg",
    )


def _profile(
    client_id: str,
    *,
    compute_multiplier: float = 1.0,
    bandwidth_mbps: float = 100.0,
    latency_ms: float = 5.0,
    availability: float = 1.0,
    memory_mb: int = 1024,
    cpu_cores: int = 4,
) -> ClientSystemProfile:
    return ClientSystemProfile(
        client_id=client_id,
        compute_multiplier=compute_multiplier,
        bandwidth_mbps=bandwidth_mbps,
        latency_ms=latency_ms,
        availability=availability,
        memory_mb=memory_mb,
        cpu_cores=cpu_cores,
    )


def test_availability_decision_is_deterministic_for_seed_round_and_client() -> None:
    policy = HeterogeneityAdmissionPolicy(
        (_profile("client-a", availability=0.5),),
        baseline_training_seconds=1.0,
        payload_bytes=1024,
        seed=17,
    )
    first = policy.evaluate_detail(_task("client-a"))
    second = policy.evaluate_detail(_task("client-a"))
    assert first == second


def test_orchestrator_skips_unavailable_resource_and_deadline_clients_before_training() -> None:
    profiles = (
        _profile("healthy"),
        _profile("offline", availability=0.0),
        _profile("tiny", memory_mb=128, cpu_cores=1),
        _profile("slow", compute_multiplier=10.0),
    )
    policy = HeterogeneityAdmissionPolicy(
        profiles,
        requirements=EdgeRequirements(min_memory_mb=256, min_cpu_cores=1),
        baseline_training_seconds=2.0,
        payload_bytes=0,
        round_deadline_seconds=5.0,
        seed=5,
    )
    trainer = RecordingTrainer()
    orchestrator = MultiprocessingOrchestrator(
        WorkerService(trainer),
        SchedulingConfig(
            mode=ExecutionMode.SYNCHRONOUS,
            target_clients=4,
            minimum_clients=1,
        ),
        admission_policy=policy,
    )
    result = orchestrator.run(
        [_task("healthy"), _task("offline"), _task("tiny"), _task("slow")]
    )

    assert trainer.clients == ["healthy"]
    assert [item.client_id for item in result.accepted] == ["healthy"]
    assert [item.client_id for item in result.skipped_tasks] == [
        "offline",
        "tiny",
        "slow",
    ]
    assert result.skip_reasons == {
        "offline": "client_unavailable",
        "tiny": "resource_ineligible",
        "slow": "round_deadline_exceeded",
    }


def test_round_summary_tracks_drop_reasons_latency_and_communication() -> None:
    tasks = (
        _task("healthy"),
        _task("offline"),
        _task("tiny"),
        _task("slow"),
    )
    policy = HeterogeneityAdmissionPolicy(
        (
            _profile("healthy", compute_multiplier=1.0),
            _profile("offline", availability=0.0),
            _profile("tiny", memory_mb=128),
            _profile("slow", compute_multiplier=10.0),
        ),
        requirements=EdgeRequirements(min_memory_mb=256),
        baseline_training_seconds=2.0,
        payload_bytes=1_000_000,
        round_deadline_seconds=5.0,
        seed=5,
    )
    summary = policy.summarize(tasks)
    assert summary.selected_clients == 4
    assert summary.admitted_clients == 1
    assert summary.unavailable_clients == 1
    assert summary.resource_ineligible_clients == 1
    assert summary.deadline_miss_clients == 1
    assert summary.mean_estimated_round_seconds > 2.0
    assert summary.max_estimated_round_seconds == summary.mean_estimated_round_seconds
    assert summary.estimated_communication_bytes == 2_000_000


def test_missing_profile_fails_closed_before_training() -> None:
    policy = HeterogeneityAdmissionPolicy(
        (_profile("known"),),
        baseline_training_seconds=1.0,
        payload_bytes=0,
    )
    decision = policy.evaluate(_task("unknown"))
    assert not decision.admitted
    assert decision.reason == "missing_system_profile"
