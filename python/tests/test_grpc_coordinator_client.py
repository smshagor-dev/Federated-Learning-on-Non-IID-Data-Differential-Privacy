"""Regression coverage for GrpcCoordinatorClient's wire mapping.

Mirrors the equivalent Go test (go/internal/coordinator/grpc_client_test.go)
and C++ integration test (cpp/coordinator/tests/coordinator_service_test.cpp):
substitutes a fake gRPC stub so the real request-building logic runs
without a live server, and asserts every field actually reaches the wire
message — see docs/create-run-wire-mapping.md.

Skips (not fails) when the generated protobuf bindings or the ``grpc``
package aren't available in this environment — the same convention
tests/baseline/test_coordinator_worker_integration.py uses for the
CLI-bridge binary. Neither is produced by the CI ``python`` job today
(that's the separate ``protobuf`` job); run ``make proto`` first (with
``grpcio``/``grpcio-tools`` installed) to exercise this locally.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import torch

from fl_platform.privacy import SampleLevelLedgerEntry
from fl_platform.privacy.budget_enforcement import (
    SampleBudgetDecision,
    SampleBudgetOutcome,
)
from fl_platform.privacy.config import SampleLevelDPConfig, SamplePrivacyBudgetPolicy
from fl_platform.rpc import generated_root_exists
from fl_platform.secure_aggregation.crypto import generate_x25519_keypair
from fl_platform.secure_aggregation.fixed_point_encoding import (
    FixedPointEncodingProfile,
)
from fl_platform.secure_aggregation.masked_update import (
    RosterContext,
    encode_weighted_delta,
    mask_weighted_delta,
)
from fl_platform.security.signing_identity import generate_signing_identity
from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    GrpcCoordinatorClient,
    PersonalizationMetricsSubmission,
    RunSpec,
)


def _grpc_available() -> bool:
    if not generated_root_exists():
        return False
    try:
        import grpc  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


class GrpcCoordinatorClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not _grpc_available():
            self.skipTest(
                "generated Python protobuf bindings or grpcio are not available; "
                "run `make proto` (with grpcio/grpcio-tools installed) first."
            )


class RecordingStub:
    """Captures the request passed to each RPC and returns a canned response."""

    def __init__(self) -> None:
        self.last_create_run_request: object | None = None
        self.last_submit_request: object | None = None
        self.last_submit_masked_update_request: object | None = None
        self.last_register_worker_request: object | None = None
        self.acquire_task_response: object | None = None

    def CreateRun(self, request: object) -> object:  # noqa: N802 - matches grpc stub naming
        self.last_create_run_request = request
        from coordinator import coordinator_pb2  # noqa: PLC0415

        return coordinator_pb2.CreateRunResponse(
            run_id=request.config.run_id,  # type: ignore[attr-defined]
            state="CREATED",
        )

    def RegisterWorker(self, request: object) -> object:  # noqa: N802
        self.last_register_worker_request = request
        from worker import worker_pb2  # noqa: PLC0415

        return worker_pb2.RegisterWorkerResponse(worker_id=request.worker_id)  # type: ignore[attr-defined]

    def AcquireTask(self, request: object) -> object:  # noqa: N802
        del request
        assert self.acquire_task_response is not None
        return self.acquire_task_response

    def SubmitClientResult(self, request: object) -> object:  # noqa: N802
        self.last_submit_request = request
        from coordinator import coordinator_pb2  # noqa: PLC0415

        return coordinator_pb2.SubmitClientResultResponse(accepted=True, reason="")

    def SubmitMaskedClientUpdate(self, request: object) -> object:  # noqa: N802
        self.last_submit_masked_update_request = request
        from coordinator import coordinator_pb2  # noqa: PLC0415

        return coordinator_pb2.SubmitMaskedClientUpdateResponse(
            accepted=True, reason=""
        )


def _make_client() -> tuple[GrpcCoordinatorClient, RecordingStub]:
    client = GrpcCoordinatorClient("127.0.0.1:0")
    stub = RecordingStub()
    client._stub = stub  # type: ignore[assignment] # substituting the real stub for a fake one, by design
    return client, stub


class GrpcCoordinatorClientCreateRunTests(GrpcCoordinatorClientTestCase):
    def test_create_run_maps_client_ids_and_hyperparameters(self) -> None:
        client, stub = _make_client()
        spec = RunSpec(
            run_id="run-1",
            algorithm="fedprox",
            weighting="sample_count",
            total_clients=3,
            target_clients_per_round=2,
            max_rounds=5,
            minimum_valid_results=2,
            client_ids=["client-a", "client-b", "client-c"],
            local_epochs=3,
            batch_size=16,
            learning_rate=0.05,
            momentum=0.9,
            weight_decay=1e-4,
            fedprox_mu=0.01,
            task_lease_seconds=90,
            max_task_retries=5,
            tensor_specs="weight:4",
            shared_parameter_names=["weight"],
        )

        client.create_run(spec, now=0.0)

        request = stub.last_create_run_request
        assert request is not None
        self.assertEqual(list(request.client_ids), ["client-a", "client-b", "client-c"])  # type: ignore[attr-defined]
        self.assertEqual(request.local_epochs, 3)  # type: ignore[attr-defined]
        self.assertEqual(request.batch_size, 16)  # type: ignore[attr-defined]
        self.assertAlmostEqual(request.learning_rate, 0.05)  # type: ignore[attr-defined]
        self.assertAlmostEqual(request.momentum, 0.9)  # type: ignore[attr-defined]
        self.assertAlmostEqual(request.weight_decay, 1e-4)  # type: ignore[attr-defined]
        self.assertAlmostEqual(request.fedprox_mu, 0.01)  # type: ignore[attr-defined]
        self.assertEqual(request.task_lease_seconds, 90)  # type: ignore[attr-defined]
        self.assertEqual(request.max_task_retries, 5)  # type: ignore[attr-defined]
        manifest = request.model_manifest  # type: ignore[attr-defined]
        self.assertEqual(len(manifest.tensors), 1)
        self.assertEqual(manifest.tensors[0].name, "weight")
        self.assertEqual(list(manifest.tensors[0].shape), [4])
        self.assertEqual(
            list(manifest.aggregation_manifest.shared_parameter_names), ["weight"]
        )


class GrpcCoordinatorClientSubmitResultTests(GrpcCoordinatorClientTestCase):
    def test_submit_result_encodes_real_tensor_values(self) -> None:
        client, stub = _make_client()
        spec = RunSpec(run_id="run-1", algorithm="fedavg")
        task = ClientTrainingTask(
            has_task=True,
            task_id="task-1",
            lease_id="lease-1",
            client_id="client-a",
            round_id=1,
            model_version="v0",
            algorithm="fedavg",
        )
        delta = {"weight": torch.tensor([1.0, 2.0, 3.0, 4.0])}

        outcome = client.submit_result(
            spec,
            worker_id="worker-a",
            task=task,
            delta=delta,
            sample_count=16,
            update_id="update-1",
            nonce="nonce-1",
            now=0.0,
        )

        self.assertTrue(outcome.accepted)
        request = stub.last_submit_request
        assert request is not None
        tensor_manifest = request.result.tensor_manifest  # type: ignore[attr-defined]
        self.assertEqual(len(tensor_manifest), 1)
        self.assertEqual(tensor_manifest[0].name, "weight")
        self.assertEqual(list(tensor_manifest[0].shape), [4])
        self.assertEqual(list(tensor_manifest[0].values), [1.0, 2.0, 3.0, 4.0])

    def test_submit_result_encodes_sample_level_privacy_entry_id(self) -> None:
        """Regression test for a real bug caught during live Docker Compose
        validation (docs/privacy-engineering-report.md): entry_id was
        computed by service.py (str(uuid.uuid4())) but never actually
        placed on the wire SampleLevelLedgerEntry, so every submitted
        entry silently arrived at the coordinator with entry_id="" no
        matter what the worker generated.
        """
        client, stub = _make_client()
        spec = RunSpec(run_id="run-1", algorithm="fedavg")
        task = ClientTrainingTask(
            has_task=True,
            task_id="task-1",
            lease_id="lease-1",
            client_id="client-a",
            round_id=1,
            model_version="v0",
            algorithm="fedavg",
        )

        client.submit_result(
            spec,
            worker_id="worker-a",
            task=task,
            delta={"weight": torch.tensor([1.0])},
            sample_count=16,
            update_id="update-1",
            nonce="nonce-1",
            now=0.0,
            sample_level_privacy=SampleLevelLedgerEntry(
                run_id="run-1",
                round_id=1,
                client_id="client-a",
                epsilon=1.5,
                delta=1e-6,
                noise_multiplier=0.9,
                sample_rate=0.25,
                steps=4,
                accountant="rdp",
                recorded_at="2026-01-01T00:00:00Z",
                entry_id="entry-abc-123",
            ),
        )

        request = stub.last_submit_request
        assert request is not None
        wire_entry = request.sample_level_privacy  # type: ignore[attr-defined]
        self.assertEqual(wire_entry.entry_id, "entry-abc-123")
        self.assertAlmostEqual(wire_entry.epsilon, 1.5)

    def test_submit_result_includes_personalization_metrics(self) -> None:
        client, stub = _make_client()
        spec = RunSpec(run_id="run-1", algorithm="ditto")
        task = ClientTrainingTask(
            has_task=True,
            task_id="task-1",
            lease_id="lease-1",
            client_id="client-a",
            round_id=1,
            model_version="v0",
            algorithm="ditto",
        )

        client.submit_result(
            spec,
            worker_id="worker-a",
            task=task,
            delta={"weight": torch.tensor([1.0])},
            sample_count=16,
            update_id="update-1",
            nonce="nonce-1",
            now=0.0,
            personalization_metrics=PersonalizationMetricsSubmission(
                global_local_accuracy=0.5,
                personalized_local_accuracy=0.7,
                sample_count=16,
            ),
        )

        request = stub.last_submit_request
        assert request is not None
        metrics = request.personalization_metrics  # type: ignore[attr-defined]
        self.assertAlmostEqual(metrics.global_local_accuracy, 0.5)
        self.assertAlmostEqual(metrics.personalized_local_accuracy, 0.7)
        self.assertEqual(metrics.sample_count, 16)


class GrpcCoordinatorClientSubmitMaskedUpdateTests(GrpcCoordinatorClientTestCase):
    """Secure Hybrid Differential Privacy Runtime slice: regression
    coverage for submit_masked_update's real hybrid wiring -- the
    sample_level_privacy/sample_privacy_decision parameters, the
    resulting signed SignedSamplePrivacyRecord envelope+payload, the
    sample_privacy_record_hash binding, and each mode's correct
    privacy_mode value on both the sample record and the user-level
    attestation (SAMPLE_LEVEL_DP=2, USER_LEVEL_DP=3, HYBRID_DP=4 per
    proto/privacy/privacy.proto). These three modes were previously
    entirely untested at the GrpcCoordinatorClient wire-mapping level
    -- only build_signed_masked_update's pure-math layer had coverage
    (test_secure_aggregation_masked_update.py)."""

    def _make_signing_client(
        self, tmp_dir: str
    ) -> tuple[GrpcCoordinatorClient, RecordingStub]:
        identity = generate_signing_identity("worker-a")
        client = GrpcCoordinatorClient(
            "127.0.0.1:0",
            signing_identity=identity,
            sequence_state_path=str(Path(tmp_dir) / "sequence-state.json"),
        )
        stub = RecordingStub()
        client._stub = stub  # type: ignore[assignment] # substituting the real stub for a fake one, by design
        return client, stub

    def _masked_payload(self) -> tuple[dict, int, object, object]:
        profile = FixedPointEncodingProfile()
        delta = {"weight": torch.tensor([1.0, -2.0])}
        encoding = encode_weighted_delta(delta, 1, profile)
        keys_self = generate_x25519_keypair()
        keys_peer = generate_x25519_keypair()
        roster = RosterContext(
            provider=2,
            protocol_version=1,
            session_id="session-1",
            run_id="run-1",
            round_id=1,
            model_version="v0",
            cohort_commitment="commitment-1",
            tensor_manifest_hash="tensor-manifest-1",
            fixed_point_profile_hash="fixed-point-1",
            cryptographic_profile_hash="crypto-profile-1",
            payload_hash="roster-payload-1",
            peer_public_keys={"worker-b": keys_peer[1]},
        )
        masked_tensors, masked_weight = mask_weighted_delta(
            encoding,
            self_worker_id="worker-a",
            self_private_key_raw=keys_self[0],
            roster=roster,
        )
        return masked_tensors, masked_weight, encoding, roster

    def _task(self, *, sample_level_dp_active: bool = False) -> ClientTrainingTask:
        return ClientTrainingTask(
            has_task=True,
            task_id="task-1",
            lease_id="lease-1",
            client_id="client-a",
            round_id=1,
            model_version="v0",
            algorithm="fedavg",
            batch_size=8,
            local_epochs=1,
            attempt=1,
            sample_level_dp_active=sample_level_dp_active,
            sample_level_privacy=(
                SampleLevelDPConfig(
                    noise_multiplier=1.0,
                    max_grad_norm=1.0,
                    target_delta=1e-5,
                    accountant="rdp",
                    poisson_sampling=True,
                    epsilon_budget=0.0,
                    sample_budget_policy=SamplePrivacyBudgetPolicy.WARN_ONLY,
                )
                if sample_level_dp_active
                else None
            ),
            secure_user_level_dp_configuration_hash="user-config-hash-1",
        )

    def _decision(self) -> SampleBudgetDecision:
        return SampleBudgetDecision(
            outcome=SampleBudgetOutcome.ALLOWED,
            policy=SamplePrivacyBudgetPolicy.WARN_ONLY,
            budget=0.0,
            current_epsilon=0.42,
            projected_epsilon=None,
            accountant_state_hash="accountant-state-hash-1",
            reason="",
        )

    def _entry(self) -> SampleLevelLedgerEntry:
        return SampleLevelLedgerEntry(
            run_id="run-1",
            round_id=1,
            client_id="client-a",
            epsilon=0.42,
            delta=1e-5,
            noise_multiplier=1.0,
            sample_rate=0.25,
            steps=4,
            accountant="rdp",
            recorded_at="2026-01-01T00:00:00Z",
            entry_id="entry-1",
        )

    def test_plain_user_level_dp_submits_no_sample_record(self) -> None:
        """secure_user_level_dp_active=True, sample_level_privacy=None
        (plain USER_LEVEL_DP, not hybrid): no sample record is built,
        sample_privacy_record_hash stays empty, and the attestation
        (still built, since user-level DP alone is active) reports
        privacy_mode=USER_LEVEL_DP (3), not HYBRID_DP (4)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            client, stub = self._make_signing_client(tmp_dir)
            masked_tensors, masked_weight, encoding, roster = self._masked_payload()
            task = self._task(sample_level_dp_active=False)

            client.submit_masked_update(
                RunSpec(run_id="run-1", algorithm="fedavg"),
                worker_id="worker-a",
                task=task,
                masked_tensors=masked_tensors,
                masked_weight=masked_weight,
                encoding=encoding,
                roster=roster,
                secure_user_level_dp_active=True,
                clip_norm=1.0,
                effective_sensitivity=1.5,
            )

            request = stub.last_submit_masked_update_request
            assert request is not None
            update = request.masked_update  # type: ignore[attr-defined]
            self.assertEqual(update.sample_privacy_record_hash, "")
            self.assertFalse(request.HasField("sample_privacy_record_envelope"))  # type: ignore[attr-defined]
            self.assertFalse(request.HasField("sample_privacy_record_payload"))  # type: ignore[attr-defined]
            self.assertTrue(update.HasField("user_level_attestation"))
            self.assertEqual(
                update.user_level_attestation.privacy_mode, 3
            )  # PRIVACY_MODE_USER_LEVEL_DP, not HYBRID_DP

    def test_hybrid_dp_submits_both_signed_records_with_hybrid_privacy_mode(
        self,
    ) -> None:
        """secure_user_level_dp_active=True AND sample_level_privacy is
        not None (real hybrid): both a signed SignedSamplePrivacyRecord
        and a signed SignedUserLevelPrivacyAttestation are built, both
        report privacy_mode=HYBRID_DP (4), and
        MaskedClientUpdate.sample_privacy_record_hash exactly equals
        the sample envelope's own payload_hash -- the binding
        SubmitMaskedClientUpdate's coordinator-side verification relies
        on (docs/secure-hybrid-dp-runtime-audit.md)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            client, stub = self._make_signing_client(tmp_dir)
            masked_tensors, masked_weight, encoding, roster = self._masked_payload()
            task = self._task(sample_level_dp_active=True)

            client.submit_masked_update(
                RunSpec(run_id="run-1", algorithm="fedavg"),
                worker_id="worker-a",
                task=task,
                masked_tensors=masked_tensors,
                masked_weight=masked_weight,
                encoding=encoding,
                roster=roster,
                secure_user_level_dp_active=True,
                clip_norm=1.0,
                effective_sensitivity=1.5,
                sample_level_privacy=self._entry(),
                sample_privacy_decision=self._decision(),
            )

            request = stub.last_submit_masked_update_request
            assert request is not None
            update = request.masked_update  # type: ignore[attr-defined]
            self.assertTrue(request.HasField("sample_privacy_record_envelope"))  # type: ignore[attr-defined]
            self.assertTrue(request.HasField("sample_privacy_record_payload"))  # type: ignore[attr-defined]
            sample_envelope = request.sample_privacy_record_envelope  # type: ignore[attr-defined]
            sample_payload = request.sample_privacy_record_payload  # type: ignore[attr-defined]
            self.assertEqual(sample_payload.privacy_mode, 4)  # PRIVACY_MODE_HYBRID_DP
            self.assertAlmostEqual(sample_payload.epsilon, 0.42)
            self.assertEqual(
                update.sample_privacy_record_hash,
                sample_envelope.payload_hash,
                "MaskedClientUpdate.sample_privacy_record_hash must equal the "
                "sample envelope's own payload_hash -- the field this slice's "
                "coordinator-side change verifies for binding",
            )
            self.assertTrue(update.HasField("user_level_attestation"))
            self.assertEqual(
                update.user_level_attestation.privacy_mode, 4
            )  # PRIVACY_MODE_HYBRID_DP, not USER_LEVEL_DP

    def test_sample_level_dp_alone_under_secagg_uses_sample_level_privacy_mode(
        self,
    ) -> None:
        """sample_level_privacy is not None but
        secure_user_level_dp_active=False (plain SAMPLE_LEVEL_DP under
        secure aggregation, no user-level layer at all): the sample
        record's privacy_mode is SAMPLE_LEVEL_DP (2), not HYBRID_DP,
        and no user_level_attestation is attached."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            client, stub = self._make_signing_client(tmp_dir)
            masked_tensors, masked_weight, encoding, roster = self._masked_payload()
            task = self._task(sample_level_dp_active=True)

            client.submit_masked_update(
                RunSpec(run_id="run-1", algorithm="fedavg"),
                worker_id="worker-a",
                task=task,
                masked_tensors=masked_tensors,
                masked_weight=masked_weight,
                encoding=encoding,
                roster=roster,
                secure_user_level_dp_active=False,
                sample_level_privacy=self._entry(),
                sample_privacy_decision=self._decision(),
            )

            request = stub.last_submit_masked_update_request
            assert request is not None
            update = request.masked_update  # type: ignore[attr-defined]
            self.assertTrue(request.HasField("sample_privacy_record_payload"))  # type: ignore[attr-defined]
            self.assertEqual(
                request.sample_privacy_record_payload.privacy_mode,
                2,  # type: ignore[attr-defined]
            )  # PRIVACY_MODE_SAMPLE_LEVEL_DP, not HYBRID_DP
            self.assertFalse(update.HasField("user_level_attestation"))

    def test_sample_level_privacy_without_decision_raises(self) -> None:
        """A missing sample_privacy_decision alongside a real
        sample_level_privacy entry is a caller contract bug -- must
        raise, never silently submit an unsigned/unaccountable sample
        record."""
        from fl_platform.security.signed_envelope import SignedEnvelopeError

        with tempfile.TemporaryDirectory() as tmp_dir:
            client, _stub = self._make_signing_client(tmp_dir)
            masked_tensors, masked_weight, encoding, roster = self._masked_payload()
            task = self._task(sample_level_dp_active=True)

            with self.assertRaises(SignedEnvelopeError):
                client.submit_masked_update(
                    RunSpec(run_id="run-1", algorithm="fedavg"),
                    worker_id="worker-a",
                    task=task,
                    masked_tensors=masked_tensors,
                    masked_weight=masked_weight,
                    encoding=encoding,
                    roster=roster,
                    secure_user_level_dp_active=True,
                    sample_level_privacy=self._entry(),
                    sample_privacy_decision=None,
                )


class GrpcCoordinatorClientRegisterWorkerTests(GrpcCoordinatorClientTestCase):
    def test_register_worker_advertises_real_opacus_state(self) -> None:
        """Regression test for docs/worker-privacy-capabilities.md: the
        coordinator's compatible-worker-only task assignment
        (RunInstance::acquire_task) depends on this capability being
        truthful, not a hardcoded True. Opacus is a real dev dependency
        in this environment (see python/pyproject.toml), so this worker
        must actually advertise supports_sample_level_dp=True with a
        real version string, not silently omit it.
        """
        client, stub = _make_client()

        spec = RunSpec(run_id="run-1", algorithm="fedavg")
        client.register_worker(spec, "worker-a", now=0.0)

        request = stub.last_register_worker_request
        assert request is not None
        privacy = request.capability.privacy  # type: ignore[attr-defined]
        self.assertTrue(privacy.supports_sample_level_dp)
        self.assertNotEqual(privacy.opacus_version, "")
        self.assertIn(1, list(privacy.supported_accountants))  # ACCOUNTANT_TYPE_RDP = 1
        # This worker uses Opacus's/PyTorch's own RNG for sample-level
        # noise, not a CSPRNG — never over-claim cryptographic security
        # here (see fl_core/privacy.hpp's matching honesty note).
        self.assertFalse(privacy.supports_secure_random)


class GrpcCoordinatorClientAcquireTaskTests(GrpcCoordinatorClientTestCase):
    def test_acquire_task_decodes_hyperparameters_and_no_task(self) -> None:
        client, stub = _make_client()
        from coordinator import coordinator_pb2
        from worker import worker_pb2

        stub.acquire_task_response = coordinator_pb2.ClientTrainingTask(
            task_available=True,
            task_id="task-1",
            lease_id="lease-1",
            local_epochs=3,
            batch_size=16,
            learning_rate=0.05,
            fedprox_mu=0.01,
            task=worker_pb2.ClientTask(
                run_id="run-1",
                round_id=2,
                client_id="client-a",
                model_version="v1",
                algorithm="fedavg",
            ),
        )

        task = client.acquire_task(
            RunSpec(run_id="run-1", algorithm="fedavg"), "worker-a", now=0.0
        )

        self.assertTrue(task.has_task)
        self.assertEqual(task.client_id, "client-a")
        self.assertEqual(task.round_id, 2)
        self.assertEqual(task.local_epochs, 3)
        self.assertEqual(task.batch_size, 16)
        self.assertAlmostEqual(task.learning_rate, 0.05)
        self.assertAlmostEqual(task.fedprox_mu, 0.01)

        stub.acquire_task_response = coordinator_pb2.ClientTrainingTask(
            task_available=False
        )
        no_task = client.acquire_task(
            RunSpec(run_id="run-1", algorithm="fedavg"), "worker-a", now=0.0
        )
        self.assertFalse(no_task.has_task)


if __name__ == "__main__":
    unittest.main()
