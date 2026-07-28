from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fl_platform.research import (
    AdaptiveClippingMode,
    BoundedExperimentOrchestrator,
    DeterminismLevel,
    ExperimentConflictError,
    ExperimentCorruptionError,
    ExperimentRegistry,
    ExperimentSpecification,
    ExperimentState,
    PartitionStrategy,
    PrivacyMode,
    RunState,
    SecureAggregationProvider,
    SyntheticExecutionResult,
    build_environment_manifest,
)
from fl_platform.research.registry import ResearchRegistryError
from fl_platform.research.specification import (
    AdaptiveClippingConfiguration,
    AlgorithmConfiguration,
    DatasetConfiguration,
    ModelConfiguration,
    PartitionConfiguration,
    PrivacyConfiguration,
    RuntimeLimits,
    SecureAggregationConfiguration,
    SeedConfiguration,
)


def _base_specification() -> ExperimentSpecification:
    spec = ExperimentSpecification(
        schema_version=1,
        experiment_id="expresearch001",
        experiment_name="FedAvg privacy comparison",
        research_question=(
            "How do privacy layers affect convergence on one fixed dataset?"
        ),
        dataset=DatasetConfiguration(
            dataset_id="cifar10",
            dataset_version="1.0",
            dataset_checksum="sha256:cifar10-demo",
            split_seed=7,
            train_split_fraction=0.8,
            validation_split_fraction=0.1,
            test_split_fraction=0.1,
        ),
        partition=PartitionConfiguration(
            strategy=PartitionStrategy.DIRICHLET,
            num_clients=5,
            seed=11,
            minimum_client_samples=4,
            alpha=0.3,
            partition_manifest_hash="manifest-hash-123",
        ),
        model=ModelConfiguration(
            model_id="groupnorm_cnn",
            model_version="v1",
            initialization_seed=19,
        ),
        algorithm=AlgorithmConfiguration(algorithm_id="fedavg"),
        privacy=PrivacyConfiguration(
            privacy_mode=PrivacyMode.USER_LEVEL,
            noise_multiplier=1.0,
            target_delta=1e-5,
            user_level_clip_norm=1.5,
            client_weighting="uniform",
        ),
        secure_aggregation=SecureAggregationConfiguration(
            provider=SecureAggregationProvider.SECAGG_NO_DROPOUT_EXPERIMENTAL,
            dropout_recovery_requested=False,
        ),
        adaptive_clipping=AdaptiveClippingConfiguration(
            mode=AdaptiveClippingMode.DISABLED
        ),
        runtime=RuntimeLimits(
            max_rounds=3,
            local_epochs=1,
            batch_size=8,
            learning_rate=0.01,
            evaluation_frequency=1,
            selected_clients_per_round=3,
        ),
        seeds=SeedConfiguration(
            seeds=[1, 2, 3],
            partition_seed=11,
            worker_assignment_seed=13,
            coordinator_seed=17,
        ),
        determinism_level=DeterminismLevel.STRICT_CPU,
    )
    spec.specification_hash = spec.compute_hash()
    return spec


class _PartialSuccessAdapter:
    def execute(self, specification: ExperimentSpecification, run_record):  # type: ignore[no-untyped-def]
        if run_record.seed == 2:
            return SyntheticExecutionResult(
                completed=False, failure_reason="seed-2 controlled failure"
            )
        return SyntheticExecutionResult(
            completed=True,
            summary={"seed": run_record.seed, "status": "ok"},
            metrics=[
                {
                    "scope": "GLOBAL",
                    "name": "accuracy",
                    "value": 0.8 + (run_record.seed * 0.01),
                    "unit": "ratio",
                    "round": 3,
                    "model_version": specification.model.model_version,
                    "source_component": "synthetic-adapter",
                    "tags": ["synthetic"],
                }
            ],
            events=[("RUN_COMPLETED", "seed finished")],
            artifact_payloads={"sanitized-log.txt": "synthetic execution completed"},
        )


class ExperimentRegistryTests(unittest.TestCase):
    def test_create_persists_immutable_specification_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            record = registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-1"
            )
            self.assertEqual(record.current_state, ExperimentState.READY.value)
            persisted = registry.get_specification_payload(spec.experiment_id)
            self.assertEqual(persisted["specification_hash"], spec.compute_hash())

    def test_unsafe_experiment_identifier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            spec.experiment_id = "../escape"
            spec.specification_hash = spec.compute_hash()
            with self.assertRaisesRegex(ResearchRegistryError, "experiment_id"):
                registry.create_experiment(
                    spec, actor="researcher", idempotency_key="create-unsafe"
                )

    def test_idempotent_create_returns_original_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            first = registry.create_experiment(
                spec, actor="researcher", idempotency_key="same-key"
            )
            second = registry.create_experiment(
                spec, actor="researcher", idempotency_key="same-key"
            )
            self.assertEqual(first.experiment_id, second.experiment_id)
            self.assertEqual(first.specification_hash, second.specification_hash)

    def test_idempotency_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            first = _base_specification()
            registry.create_experiment(
                first, actor="researcher", idempotency_key="same-key"
            )
            second = _base_specification()
            second.experiment_id = "expresearch002"
            second.specification_hash = second.compute_hash()
            with self.assertRaises(ExperimentConflictError):
                registry.create_experiment(
                    second, actor="researcher", idempotency_key="same-key"
                )

    def test_valid_and_invalid_state_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            created = registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-2"
            )
            running = registry.transition_experiment_state(
                spec.experiment_id,
                ExperimentState.RUNNING,
                actor="system",
                reason="manual start",
                expected_version=created.record_version,
            )
            self.assertEqual(running.current_state, ExperimentState.RUNNING.value)
            with self.assertRaisesRegex(
                ResearchRegistryError, "invalid experiment transition"
            ):
                registry.transition_experiment_state(
                    spec.experiment_id,
                    ExperimentState.CREATED,
                    actor="system",
                    reason="illegal rewind",
                    expected_version=running.record_version,
                )

    def test_optimistic_concurrency_conflict_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            created = registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-3"
            )
            registry.transition_experiment_state(
                spec.experiment_id,
                ExperimentState.RUNNING,
                actor="system",
                reason="start",
                expected_version=created.record_version,
            )
            with self.assertRaises(ExperimentConflictError):
                registry.transition_experiment_state(
                    spec.experiment_id,
                    ExperimentState.CANCEL_REQUESTED,
                    actor="system",
                    reason="stale write",
                    expected_version=created.record_version,
                )

    def test_metric_append_and_corruption_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-4"
            )
            run_record = registry.get_run_record(spec.experiment_id, 1)
            registry.append_metric(
                spec.experiment_id,
                1,
                run_id=run_record.run_id,
                scope="GLOBAL",
                metric_name="accuracy",
                value=0.9,
                unit="ratio",
                round_index=1,
                model_version="v1",
                source_component="test",
            )
            metrics_path = (
                registry.experiment_directory(spec.experiment_id)
                / "runs"
                / "seed-1"
                / "metrics.jsonl"
            )
            with metrics_path.open("a", encoding="utf-8") as handle:
                handle.write("{not-json}\n")
            metrics, recovered = registry.list_metrics(spec.experiment_id, 1)
            self.assertEqual(len(metrics), 1)
            self.assertEqual(recovered, 1)

    def test_corruption_detection_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-5"
            )
            specification_path = (
                registry.experiment_directory(spec.experiment_id) / "specification.json"
            )
            specification_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ExperimentCorruptionError):
                registry.get_specification_payload(spec.experiment_id)

    def test_retry_lineage_preserves_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-6"
            )
            registry.transition_run_state(
                spec.experiment_id, 1, RunState.PREPARING, reason="prep"
            )
            registry.transition_run_state(
                spec.experiment_id, 1, RunState.RUNNING, reason="run"
            )
            failed = registry.transition_run_state(
                spec.experiment_id, 1, RunState.FAILED, reason="controlled failure"
            )
            self.assertEqual(failed.failure_count, 1)
            retried = registry.create_retry_attempt(
                spec.experiment_id, 1, actor="researcher", reason="retry after failure"
            )
            self.assertEqual(retried.run_attempt, 2)
            self.assertEqual(retried.retry_lineage, [1])
            self.assertEqual(retried.current_state, RunState.CREATED.value)

    def test_cancellation_marks_unstarted_seeds_and_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            created = registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-7"
            )
            canceled = registry.request_cancel(
                spec.experiment_id,
                actor="researcher",
                expected_version=created.record_version,
            )
            self.assertEqual(
                canceled.current_state, ExperimentState.CANCEL_REQUESTED.value
            )
            self.assertEqual(
                registry.get_run_record(spec.experiment_id, 2).current_state,
                RunState.CANCELED.value,
            )

    def test_restart_recovery_marks_inflight_runs_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-8"
            )
            registry.transition_run_state(
                spec.experiment_id, 1, RunState.PREPARING, reason="prep"
            )
            registry.transition_run_state(
                spec.experiment_id, 1, RunState.RUNNING, reason="run"
            )
            recovery = registry.recover()
            self.assertEqual(recovery["stale_run_count"], 1)
            self.assertEqual(
                registry.get_run_record(spec.experiment_id, 1).current_state,
                RunState.LOST.value,
            )

    def test_bounded_orchestration_preserves_failed_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            registry.create_experiment(
                spec,
                actor="researcher",
                idempotency_key="create-9",
                environment_manifest=build_environment_manifest(spec),
            )
            orchestrator = BoundedExperimentOrchestrator(
                registry, _PartialSuccessAdapter()
            )
            final_record = orchestrator.execute_experiment(spec.experiment_id)
            self.assertEqual(
                final_record.current_state,
                ExperimentState.COMPLETED_WITH_PARTIAL_RUNS.value,
            )
            self.assertEqual(
                registry.get_run_record(spec.experiment_id, 2).current_state,
                RunState.FAILED.value,
            )
            self.assertEqual(
                registry.get_run_record(spec.experiment_id, 1).current_state,
                RunState.COMPLETED.value,
            )
            metrics, recovered = registry.list_metrics(spec.experiment_id, 1)
            self.assertEqual(recovered, 0)
            self.assertEqual(len(metrics), 1)

    def test_duplicate_seed_list_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            spec.seeds.seeds = [1, 1, 2]
            spec.specification_hash = spec.compute_hash()
            with self.assertRaisesRegex(ResearchRegistryError, "duplicates"):
                registry.create_experiment(
                    spec, actor="researcher", idempotency_key="create-10"
                )

    def test_registered_artifact_manifest_contains_specification_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ExperimentRegistry(Path(temp_dir) / "research")
            spec = _base_specification()
            registry.create_experiment(
                spec, actor="researcher", idempotency_key="create-11"
            )
            manifest_path = (
                registry.experiment_directory(spec.experiment_id) / "artifacts.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact_types = {entry["artifact_type"] for entry in manifest["entries"]}
            self.assertIn("specification", artifact_types)
            self.assertIn("environment_manifest", artifact_types)


if __name__ == "__main__":
    unittest.main()
