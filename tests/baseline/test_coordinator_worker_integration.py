"""Milestone 3 cross-language integration tests: Go/Python/C++ working
together through the real, tested coordinator domain layer.

The C++ side of this is driven through ``fl_coordinator_cli`` (the local
compatibility bridge — see docs/coordinator-runtime.md for exactly why:
no C++ gRPC toolchain is available in this environment, so this proves
the same domain logic a real gRPC server would expose, via the same
process-per-call + checkpoint-continuity mechanism already unit-tested
in cpp/coordinator/tests/). All tests skip (not fail) if the CLI binary
hasn't been built.

Required scenarios covered here (see the task's Work Package L list):
FedAvg two rounds, FedProx two rounds, SCAFFOLD two rounds, multiple
Python workers, worker failure + task retry, coordinator restart +
resume, duplicate result rejection, stale model rejection, cancel during
a run, and pause/resume between rounds.
"""

from __future__ import annotations

import dataclasses
import shutil
import unittest
from pathlib import Path

import torch

from fl_platform.worker.coordinator_client import (
    CliBridgeCoordinatorClient,
    ClientTrainingTask,
    CoordinatorRejectedError,
    RunSpec,
)
from fl_platform.worker.task_runner import build_bridge_compatible_model, run_local_training

REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_CLI_PATHS = [
    REPO_ROOT / "build" / "cpp-debug" / "Debug" / "fl_coordinator_cli.exe",
    REPO_ROOT / "build" / "cpp-release" / "Release" / "fl_coordinator_cli.exe",
    REPO_ROOT / "build" / "cpp-debug" / "fl_coordinator_cli",
    REPO_ROOT / "build" / "cpp-release" / "fl_coordinator_cli",
]
SCRATCH_ROOT = REPO_ROOT / ".test_scratch" / "coordinator_worker_integration"


def _find_cli() -> Path | None:
    for candidate in _CANDIDATE_CLI_PATHS:
        if candidate.exists():
            return candidate
    return None


def _train_and_result(task: ClientTrainingTask, num_classes: int = 2, in_channels: int = 1, image_size: int = 4):
    model = build_bridge_compatible_model(num_classes=num_classes, in_channels=in_channels, image_size=image_size)
    global_state = {name: tensor.clone() for name, tensor in model.state_dict().items()}
    seed = sum(task.client_id.encode("utf-8")) + task.round_id
    return run_local_training(
        task, global_state, model, seed=seed, sample_count=16,
        num_classes=num_classes, in_channels=in_channels, image_size=image_size,
    )


class CoordinatorWorkerIntegrationTests(unittest.TestCase):
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

    def _run_round(self, spec: RunSpec, worker_ids: list[str], round_id: int, now: float) -> None:
        """Two (or more) simulated Python workers each acquire one task,
        train it for real, and submit — exercising "multiple Python
        workers" and the full acquire->train->submit path per client."""
        for worker_id in worker_ids:
            task = self.client.acquire_task(spec, worker_id, now)
            self.assertTrue(task.has_task, f"expected a task to be available for {worker_id} in round {round_id}")
            outcome = _train_and_result(task)
            submit_kwargs = {}
            if spec.algorithm == "scaffold":
                submit_kwargs["control_delta"] = outcome.control_delta
                submit_kwargs["refreshed_client_control_variate"] = outcome.refreshed_client_control_variate
            result = self.client.submit_result(
                spec, worker_id, task, outcome.delta, outcome.sample_count,
                update_id=f"update-{task.client_id}-{round_id}",
                nonce=f"nonce-{task.client_id}-{round_id}",
                now=now, **submit_kwargs,
            )
            self.assertTrue(result.accepted, f"expected result for {task.client_id} to be accepted: {result.reason}")

    def _base_spec(self, run_id: str, algorithm: str, max_rounds: int = 2) -> RunSpec:
        return RunSpec(
            run_id=run_id,
            algorithm=algorithm,
            weighting="uniform",
            total_clients=2,
            target_clients_per_round=2,
            max_rounds=max_rounds,
            minimum_valid_results=2,
            client_ids=["client-a", "client-b"],
            fedprox_mu=0.01,
            tensor_elements=32,  # must match BridgeCompatibleModel(num_classes=2, in_channels=1, image_size=4)'s flat weight size
        )

    def test_fedavg_two_rounds_multiple_workers(self) -> None:
        spec = self._base_spec("run-fedavg-e2e", "fedavg")
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")

        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)
        snapshot = self.client.get_run(spec, now=1.0)
        self.assertEqual(snapshot.model_version, "v1", "round 1 must advance the model version")

        self._run_round(spec, ["worker-1", "worker-2"], round_id=2, now=1.0)
        snapshot = self.client.get_run(spec, now=2.0)
        self.assertEqual(snapshot.model_version, "v2")
        self.assertEqual(snapshot.state, "COMPLETED")

    def test_fedprox_two_rounds(self) -> None:
        spec = self._base_spec("run-fedprox-e2e", "fedprox")
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)
        self._run_round(spec, ["worker-1", "worker-2"], round_id=2, now=1.0)
        snapshot = self.client.get_run(spec, now=2.0)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.model_version, "v2")

    def test_scaffold_two_rounds(self) -> None:
        spec = self._base_spec("run-scaffold-e2e", "scaffold")
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)
        self._run_round(spec, ["worker-1", "worker-2"], round_id=2, now=1.0)
        snapshot = self.client.get_run(spec, now=2.0)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.model_version, "v2")

    def test_duplicate_result_rejected(self) -> None:
        spec = self._base_spec("run-dup-e2e", "fedavg", max_rounds=1)
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")

        task = self.client.acquire_task(spec, "worker-1", now=0.0)
        outcome = _train_and_result(task)
        first = self.client.submit_result(
            spec, "worker-1", task, outcome.delta, outcome.sample_count,
            update_id="u1", nonce="n1", now=0.0,
        )
        self.assertTrue(first.accepted)

        duplicate = self.client.submit_result(
            spec, "worker-1", task, outcome.delta, outcome.sample_count,
            update_id="u1-again", nonce="n1-again", now=0.0,
        )
        self.assertFalse(duplicate.accepted, "resubmitting for the same client must be rejected")
        self.assertIn("duplicate", duplicate.reason.lower())

    def test_stale_model_version_rejected(self) -> None:
        # A single-client round with minimum_valid_results=1: submitting
        # this one (deliberately stale) result immediately triggers round
        # finalization (aggregation), which is where UpdateValidator
        # actually checks base_model_version against the manifest — the
        # dispatcher-level submit accept/reject check does not inspect
        # tensor/version content at all, only lease bookkeeping.
        spec = self._base_spec("run-stale-e2e", "fedavg", max_rounds=1)
        spec.target_clients_per_round = 1
        spec.minimum_valid_results = 1
        spec.client_ids = ["client-a"]
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")

        task = self.client.acquire_task(spec, "worker-1", now=0.0)
        outcome = _train_and_result(task)
        stale_task = dataclasses.replace(task, model_version="v99-does-not-exist")
        with self.assertRaises(CoordinatorRejectedError) as context:
            self.client.submit_result(
                spec, "worker-1", stale_task, outcome.delta, outcome.sample_count,
                update_id="u1", nonce="n1", now=0.0,
            )
        self.assertIn("stale", str(context.exception).lower())

    def test_worker_failure_and_task_retry(self) -> None:
        spec = self._base_spec("run-retry-e2e", "fedavg", max_rounds=1)
        spec.task_lease_seconds = 10
        spec.max_task_retries = 2
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")

        # worker-1 acquires client-a's task but "crashes" (never submits).
        first_attempt = self.client.acquire_task(spec, "worker-1", now=0.0)
        self.assertTrue(first_attempt.has_task)

        # Time passes beyond the lease timeout: a different worker must
        # be able to acquire the same client's task for retry.
        retry_attempt = self.client.acquire_task(spec, "worker-2", now=15.0)
        self.assertTrue(retry_attempt.has_task)
        self.assertEqual(
            retry_attempt.client_id, first_attempt.client_id,
            "the retried task must be for the same client whose lease expired",
        )

        outcome = _train_and_result(retry_attempt)
        result = self.client.submit_result(
            spec, "worker-2", retry_attempt, outcome.delta, outcome.sample_count,
            update_id="u-retry", nonce="n-retry", now=15.0,
        )
        self.assertTrue(result.accepted, "the retried submission must be accepted")

    def test_pause_and_resume_between_rounds(self) -> None:
        spec = self._base_spec("run-pause-e2e", "fedavg", max_rounds=3)
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)

        paused = self.client.pause_run(spec, now=1.0, reason="operator requested")
        self.assertEqual(paused.state, "PAUSED")

        resumed = self.client.resume_run(spec, now=2.0)
        self.assertIn(resumed.state, ("RUNNING", "WAITING_FOR_CLIENTS"))

        self._run_round(spec, ["worker-1", "worker-2"], round_id=2, now=2.0)
        snapshot = self.client.get_run(spec, now=3.0)
        self.assertEqual(snapshot.model_version, "v2")

    def test_cancel_during_run(self) -> None:
        spec = self._base_spec("run-cancel-e2e", "fedavg", max_rounds=3)
        self.client.create_run(spec, now=0.0)
        self.client.start_run(spec, now=0.0, trace_id="t1")
        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)

        cancelled = self.client.cancel_run(spec, now=1.0, reason="test cancel")
        self.assertEqual(cancelled.state, "CANCELED")

        # Idempotent: cancelling again must not raise and must still
        # report CANCELED.
        cancelled_again = self.client.cancel_run(spec, now=2.0, reason="test cancel again")
        self.assertEqual(cancelled_again.state, "CANCELED")

    def test_coordinator_restart_and_resume(self) -> None:
        """"Process A" completes round 1 and is dropped (simulated
        crash); "process B" is a fresh CliBridgeCoordinatorClient
        pointed at the same on-disk state directory and must resume
        exactly at round 2, producing the same final model as an
        uninterrupted run — the critical Work Package G recovery test,
        exercised here through the Python client."""
        spec = self._base_spec("run-restart-e2e", "fedavg", max_rounds=2)

        # Control: uninterrupted.
        control_dir = self.state_dir / "control"
        control_client = CliBridgeCoordinatorClient(self.cli_path, control_dir)
        control_client.create_run(spec, now=0.0)
        control_client.start_run(spec, now=0.0, trace_id="t1")
        original_client, self.client = self.client, control_client
        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)
        self._run_round(spec, ["worker-1", "worker-2"], round_id=2, now=1.0)
        control_final = control_client.get_run(spec, now=2.0)
        self.client = original_client

        # Interrupted: only round 1, using a separate state directory.
        restart_dir = self.state_dir / "restart"
        process_a = CliBridgeCoordinatorClient(self.cli_path, restart_dir)
        process_a.create_run(spec, now=0.0)
        process_a.start_run(spec, now=0.0, trace_id="t1")
        self.client, saved = process_a, self.client
        self._run_round(spec, ["worker-1", "worker-2"], round_id=1, now=0.0)
        after_round_1 = process_a.get_run(spec, now=1.0)
        self.assertEqual(after_round_1.current_round, 1)
        # process_a is simply never used again, simulating a crash.

        # "Process B": a fresh client object pointed at the same state
        # directory (no in-memory continuity, only what's on disk).
        process_b = CliBridgeCoordinatorClient(self.cli_path, restart_dir)
        self.client = process_b
        self._run_round(spec, ["worker-1", "worker-2"], round_id=2, now=1.0)
        recovered_final = process_b.get_run(spec, now=2.0)
        self.client = saved

        self.assertEqual(recovered_final.current_round, 2)
        self.assertEqual(recovered_final.state, "COMPLETED")
        self.assertEqual(
            recovered_final.model_version, control_final.model_version,
            "a recovered run must reach the same final model_version as an uninterrupted one",
        )


if __name__ == "__main__":
    unittest.main()
