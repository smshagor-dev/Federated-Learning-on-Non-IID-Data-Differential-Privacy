"""Algorithm Expansion phase cross-language integration tests: FedSAM, Ditto, and
Per-FedAvg driven through the real C++ coordinator (via fl_coordinator_cli
— see test_coordinator_worker_integration.py's module docstring for why
the CLI bridge, not a live gRPC server, is what's exercised locally) and
the real Python algorithm registry (fl_platform.algorithms), not the
the Foundation phase-era scalar placeholders.

Required scenarios (see the task's Work Package Q list): two rounds per
algorithm, two-plus workers, four-plus synthetic clients, personalized
checkpoint persistence + reload across a simulated worker restart,
wrong-client checkpoint rejection, model schema mismatch rejection,
shared-backbone local-head tensor rejection, and coordinator restart
between rounds. Existing FedAvg/FedProx/SCAFFOLD regression coverage
lives in test_coordinator_worker_integration.py and is untouched.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import torch

from fl_platform.algorithms import LocalTrainingContext, get_algorithm
from fl_platform.algorithms.base import LocalTrainingResult
from fl_platform.models.factory import PersonalizableBridgeModel
from fl_platform.personalization import (
    CURRENT_SCHEMA_VERSION,
    FilesystemPersonalizedModelStore,
    PersonalizedModelOwnershipError,
    PersonalizedModelRecord,
)
from fl_platform.worker.coordinator_client import (
    CliBridgeCoordinatorClient,
    ClientTrainingTask,
    PersonalizationMetricsSubmission,
    RunSpec,
)
from fl_platform.worker.dataset_loader import PartitionManifest
from fl_platform.worker.task_runner import build_bridge_compatible_model

REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_CLI_PATHS = [
    REPO_ROOT / "build" / "cpp-debug" / "Debug" / "fl_coordinator_cli.exe",
    REPO_ROOT / "build" / "cpp-release" / "Release" / "fl_coordinator_cli.exe",
    REPO_ROOT / "build" / "cpp-debug" / "fl_coordinator_cli",
    REPO_ROOT / "build" / "cpp-release" / "fl_coordinator_cli",
]
SCRATCH_ROOT = REPO_ROOT / ".test_scratch" / "algorithm_expansion_integration"


def _find_cli() -> Path | None:
    for candidate in _CANDIDATE_CLI_PATHS:
        if candidate.exists():
            return candidate
    return None


def _make_context(
    algorithm: str,
    task: ClientTrainingTask,
    algorithm_config: dict,
    model: torch.nn.Module,
    personalized_model: torch.nn.Module | None = None,
) -> LocalTrainingContext:
    partition = PartitionManifest(
        dataset_id="synthetic",
        partition_id=f"partition-{task.client_id}",
        client_id=task.client_id,
        sample_count=16,
        seed=sum(task.client_id.encode("utf-8")) + task.round_id,
        num_classes=2,
        in_channels=1,
        image_size=4,
    )
    return LocalTrainingContext(
        run_id="", round_id=task.round_id, client_id=task.client_id, task_id=task.task_id,
        algorithm=algorithm, model_version=task.model_version, global_model=model,
        dataset_partition=partition, device=torch.device("cpu"), seed=partition.seed,
        algorithm_config=algorithm_config, optimizer_config={}, evaluation_config={},
        personalized_model=personalized_model,
    )


class AlgorithmExpansionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        cli_path = _find_cli()
        if cli_path is None:
            self.skipTest(
                "fl_coordinator_cli has not been built. Run "
                "`cmake --build build/cpp-debug --target fl_coordinator_cli` first."
            )
        self.cli_path = cli_path
        self.state_dir = SCRATCH_ROOT / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.state_dir, ignore_errors=True)
        self.client = CliBridgeCoordinatorClient(cli_path, self.state_dir)
        self.store_dir = tempfile.mkdtemp(prefix="fl_m4_personalized_")
        self.addCleanup(lambda: shutil.rmtree(self.store_dir, ignore_errors=True))

    def _base_spec(self, run_id: str, algorithm: str, max_rounds: int = 2) -> RunSpec:
        return RunSpec(
            run_id=run_id,
            algorithm=algorithm,
            weighting="uniform",
            total_clients=4,
            target_clients_per_round=4,
            max_rounds=max_rounds,
            minimum_valid_results=4,
            client_ids=["client-a", "client-b", "client-c", "client-d"],
            tensor_elements=32,  # matches build_bridge_compatible_model(num_classes=2, in_channels=1, image_size=4)
        )

    def _model(self) -> torch.nn.Module:
        return build_bridge_compatible_model(num_classes=2, in_channels=1, image_size=4)

    # ------------------------------------------------------------------ #
    # FedSAM: real two-pass SAM training through the real coordinator.
    # ------------------------------------------------------------------ #
    def test_fedsam_two_rounds_four_clients(self) -> None:
        spec = self._base_spec("run-fedsam-e2e", "fedsam")
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        algo = get_algorithm("fedsam")
        worker_ids = ["worker-1", "worker-2", "worker-1", "worker-2"]

        for round_id in (1, 2):
            now = float(round_id - 1)
            for worker_id in worker_ids:
                task = self.client.acquire_task(spec, worker_id, now)
                self.assertTrue(task.has_task, f"round {round_id}: expected a task for {worker_id}")
                context = _make_context(
                    "fedsam", task, {"rho": 0.05, "local_epochs": 1, "learning_rate": 0.05, "batch_size": 4},
                    self._model(),
                )
                algo.validate_task(context)
                result = algo.train(context)
                self.assertFalse(result.is_non_finite)
                outcome_result = self.client.submit_result(
                    spec, worker_id, task, result.global_update, result.sample_count,
                    update_id=f"update-{task.client_id}-{round_id}", nonce=f"nonce-{task.client_id}-{round_id}",
                    now=now,
                )
                self.assertTrue(outcome_result.accepted, outcome_result.reason)

        snapshot = self.client.get_run(spec, now=2.0)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.model_version, "v2")

    # ------------------------------------------------------------------ #
    # Ditto: global + personalized training, personalized checkpoint
    # persists and reloads across a simulated worker restart.
    # ------------------------------------------------------------------ #
    def test_ditto_two_rounds_with_personalized_checkpoint_persistence(self) -> None:
        spec = self._base_spec("run-ditto-e2e", "ditto")
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        algo = get_algorithm("ditto")
        algorithm_config = {
            "regularization_coefficient": 0.5,
            "global_local_epochs": 1,
            "personalized_local_epochs": 1,
            "global_learning_rate": 0.05,
            "personalized_learning_rate": 0.05,
            "batch_size": 4,
        }
        worker_ids = ["worker-1", "worker-2", "worker-1", "worker-2"]
        store = FilesystemPersonalizedModelStore(self.store_dir)
        architecture_hash = "test-arch-hash"

        for round_id in (1, 2):
            now = float(round_id - 1)
            for worker_id in worker_ids:
                task = self.client.acquire_task(spec, worker_id, now)
                self.assertTrue(task.has_task, f"round {round_id}: expected a task for {worker_id}")

                # Simulates "worker restart between rounds": the
                # personalized model is loaded fresh from disk every
                # round, never kept in worker memory across rounds.
                existing = store.load("run-ditto-e2e", task.client_id, "ditto")
                personalized_model = None
                if existing is not None:
                    personalized_model = self._model()
                    personalized_model.load_state_dict(existing.state_dict)

                model = self._model()
                context = _make_context(
                    "ditto", task, algorithm_config, model, personalized_model=personalized_model
                )
                algo.validate_task(context)
                result: LocalTrainingResult = algo.train(context)
                self.assertIsNotNone(result.personalized_checkpoint)

                store.save(
                    PersonalizedModelRecord(
                        schema_version=CURRENT_SCHEMA_VERSION,
                        run_id="run-ditto-e2e",
                        client_id=task.client_id,
                        algorithm="ditto",
                        global_model_version=task.model_version,
                        personalized_model_version=round_id,
                        architecture_name="bridge_compatible",
                        state_dict_schema_hash=architecture_hash,
                        state_dict=result.personalized_checkpoint,
                        training_metrics=result.algorithm_metrics,
                    )
                )

                outcome_result = self.client.submit_result(
                    spec, worker_id, task, result.global_update, result.sample_count,
                    update_id=f"update-{task.client_id}-{round_id}", nonce=f"nonce-{task.client_id}-{round_id}",
                    now=now,
                    personalization_metrics=PersonalizationMetricsSubmission(
                        global_local_accuracy=0.5,
                        personalized_local_accuracy=0.6,
                        sample_count=result.sample_count,
                        personalized_model_version=round_id,
                    ),
                )
                self.assertTrue(outcome_result.accepted, outcome_result.reason)

        snapshot = self.client.get_run(spec, now=2.0)
        self.assertEqual(snapshot.state, "COMPLETED")

        # Personalized checkpoints for all four clients persisted at v2.
        for client_id in spec.client_ids:
            record = store.load("run-ditto-e2e", client_id, "ditto")
            self.assertIsNotNone(record)
            self.assertEqual(record.personalized_model_version, 2)

        # Wrong-client checkpoint access is rejected, not silently served.
        with self.assertRaises(PersonalizedModelOwnershipError):
            tampered_path = Path(self.store_dir) / "run-ditto-e2e" / "client-a" / "ditto.json"
            content = tampered_path.read_text(encoding="utf-8")
            tampered_path.write_text(content.replace("client-a", "client-x"), encoding="utf-8")
            store.load("run-ditto-e2e", "client-a", "ditto")

        # Personalization summary is retrievable through the coordinator.
        summary = self.client.get_personalization_summary(spec, now=2.0)
        self.assertEqual(len(summary), 4)
        for record in summary:
            self.assertAlmostEqual(record.personalized_improvement, 0.1, places=6)

    # ------------------------------------------------------------------ #
    # Per-FedAvg: deterministic support/query adaptation + meta-update.
    # ------------------------------------------------------------------ #
    def test_per_fedavg_two_rounds_four_clients(self) -> None:
        spec = self._base_spec("run-per-fedavg-e2e", "per_fedavg")
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        algo = get_algorithm("per_fedavg")
        worker_ids = ["worker-1", "worker-2", "worker-1", "worker-2"]

        for round_id in (1, 2):
            now = float(round_id - 1)
            for worker_id in worker_ids:
                task = self.client.acquire_task(spec, worker_id, now)
                self.assertTrue(task.has_task, f"round {round_id}: expected a task for {worker_id}")
                context = _make_context(
                    "per_fedavg", task,
                    {
                        "inner_learning_rate": 0.05, "outer_learning_rate": 0.05,
                        "inner_steps": 2, "meta_steps": 1, "minimum_samples_required": 4, "batch_size": 4,
                    },
                    self._model(),
                )
                algo.validate_task(context)
                result = algo.train(context)
                self.assertEqual(result.algorithm_metrics["skipped_client"], 0.0)
                outcome_result = self.client.submit_result(
                    spec, worker_id, task, result.global_update, result.sample_count,
                    update_id=f"update-{task.client_id}-{round_id}", nonce=f"nonce-{task.client_id}-{round_id}",
                    now=now,
                )
                self.assertTrue(outcome_result.accepted, outcome_result.reason)

        snapshot = self.client.get_run(spec, now=2.0)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.model_version, "v2")

    # ------------------------------------------------------------------ #
    # Shared-backbone / local-head aggregation manifest enforcement,
    # through the real coordinator (not just the C++ unit test).
    # ------------------------------------------------------------------ #
    def test_local_head_tensor_rejected_by_coordinator(self) -> None:
        spec = RunSpec(
            run_id="run-manifest-e2e",
            algorithm="ditto",
            total_clients=1,
            target_clients_per_round=1,
            max_rounds=1,
            minimum_valid_results=1,
            client_ids=["client-a"],
            # Only the aggregatable tensor belongs in the run's canonical
            # ModelManifest — "head" is declared personalized-only and
            # must never appear here (see docs/aggregation-manifests.md);
            # a client is still free to try submitting it anyway, which
            # is exactly what this test attempts and expects rejected.
            tensor_specs="backbone:8",
            shared_parameter_names=["backbone"],
            personalized_parameter_names=["head"],
        )
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        task = self.client.acquire_task(spec, "worker-1", now=0.0)
        self.assertTrue(task.has_task)

        model = PersonalizableBridgeModel(num_classes=2, in_channels=1, image_size=2, embedding_dim=2)
        state = model.state_dict()

        # Attempt to submit BOTH the shared backbone and the personalized
        # head — the coordinator must reject this outright, per
        # docs/aggregation-manifests.md.
        result = self.client.submit_result(
            spec, "worker-1", task, {"backbone": state["backbone"], "head": state["head"]},
            sample_count=4, update_id="u1", nonce="n1", now=1.0,
        )
        self.assertFalse(result.accepted)
        self.assertIn("head", result.reason)
        self.assertIn("personalized-only", result.reason)

        # Shared-only submission succeeds.
        result2 = self.client.submit_result(
            spec, "worker-1", task, {"backbone": state["backbone"]},
            sample_count=4, update_id="u2", nonce="n2", now=1.0,
        )
        self.assertTrue(result2.accepted, result2.reason)


if __name__ == "__main__":
    unittest.main()
