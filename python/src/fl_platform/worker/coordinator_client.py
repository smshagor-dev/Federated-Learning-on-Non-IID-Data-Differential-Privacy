"""Coordinator client abstraction for the Python worker.

Two backends are provided behind the same :class:`CoordinatorClient`
protocol:

* :class:`CliBridgeCoordinatorClient` drives the real, unit-tested C++
  coordinator domain layer (``fl_coordinator``) through
  ``cpp/coordinator/tools/coordinator_cli.cpp`` — one process per call,
  with state continuity provided by the coordinator's own checkpoint/
  recovery machinery (see docs/coordinator-recovery.md). This is what
  actually runs in this environment, both for local development and for
  the cross-language integration tests, because no C++ gRPC build is
  available here (see docs/coordinator-runtime.md for exactly why).
* :class:`GrpcCoordinatorClient` is a real ``grpcio`` client against the
  generated ``coordinator_pb2_grpc`` stubs, for a long-lived C++ gRPC
  coordinator server. It is genuine, runnable code, but has not been
  exercised against a real server in this environment for the same
  reason — it is intended to be validated in CI, where the C++ gRPC
  server can actually be built (apt-installable ``libgrpc++-dev``).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import torch

from fl_platform.privacy import (
    SUPPORTED_ACCOUNTANTS,
    SampleLevelDPConfig,
    SampleLevelLedgerEntry,
    opacus_capabilities,
    worker_reports_secure_random_support,
)
from fl_platform.privacy.budget_enforcement import SampleBudgetDecision
from fl_platform.privacy.secure_random import opacus_secure_mode_available
from fl_platform.secure_aggregation.key_advertisement import (
    FrozenCohortRosterLike,
    build_signed_key_advertisement,
)
from fl_platform.secure_aggregation.masked_update import build_signed_masked_update
from fl_platform.secure_aggregation.user_level_attestation import (
    build_signed_user_level_privacy_attestation,
)
from fl_platform.security import security_event as _security_event
from fl_platform.security import transport
from fl_platform.security.capability_statement import (
    CapabilityStatementPayload,
    sign_capability_statement,
)
from fl_platform.security.coordinator_task_replay import CoordinatorTaskReplayStore
from fl_platform.security.coordinator_task_signing import (
    AggregationManifestFields,
    SampleLevelPrivacyFields,
    SecureAggregationTaskBindingFields,
    SignedCoordinatorTaskFields,
    TaskConfigurationFields,
    TensorDescriptor,
)
from fl_platform.security.coordinator_task_verifier import (
    CoordinatorTaskRejectedError,
    CoordinatorTaskRejectionReason,
    CoordinatorTaskVerificationParams,
    verify_coordinator_task,
)
from fl_platform.security.coordinator_trust_bundle import (
    BundleReloadResult,
    TrustedCoordinatorKey,
    TrustedCoordinatorKeyBundleReloader,
)
from fl_platform.security.security_event_journal import SecurityEventJournal
from fl_platform.security.sequence_state import SequenceStateStore
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_CLIENT_RESULT,
    MESSAGE_STREAM_KEY_MANAGEMENT,
    MESSAGE_STREAM_PRIVACY_RECORD,
    MESSAGE_STREAM_SECURITY_EVENTS,
    MESSAGE_TYPE_CLIENT_RESULT,
    MESSAGE_TYPE_KEY_ROTATION_REQUEST,
    MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD,
    MESSAGE_TYPE_SECURITY_EVENT_BATCH,
    EnvelopeFields,
    SamplePrivacyRecordFields,
    SignedEnvelopeError,
    WorkerKeyRotationFields,
    WorkerSecurityEventBatchFields,
    WorkerSecurityEventFields,
    client_result_payload_hash_input,
    make_nonce,
    rotation_payload_hash_input,
    sample_privacy_configuration_hash,
    sample_privacy_record_payload_hash_input,
    security_event_batch_payload_hash_input,
    sha256_hex,
    sign_envelope,
)
from fl_platform.security.signed_envelope import (
    now as security_now,
)
from fl_platform.security.signing_identity import WorkerSigningIdentity
from fl_platform.security.transport import WorkerTLSConfig
from fl_platform.worker.security_event_queue import WorkerSecurityEventQueue
from fl_platform.worker.task_journal import (
    AcceptedTaskJournal,
    DuplicateTaskExecutionError,
)
from fl_platform.worker.tensor_codec import decode_tensor_dict, encode_named_tensor

logger = logging.getLogger("fl_platform.worker.coordinator_client")


class CoordinatorUnavailableError(RuntimeError):
    """Raised when the coordinator (CLI bridge or gRPC server) cannot be reached."""


class CoordinatorRejectedError(RuntimeError):
    """Raised when the coordinator explicitly rejects a request (not a
    transport failure)."""


@dataclass(slots=True)
class RunSnapshot:
    run_id: str
    state: str
    current_round: int
    max_rounds: int
    model_version: str
    algorithm: str
    registered_workers: int
    healthy_workers: int


@dataclass(slots=True)
class ClientTrainingTask:
    has_task: bool
    task_id: str = ""
    lease_id: str = ""
    client_id: str = ""
    round_id: int = 0
    model_version: str = ""
    algorithm: str = ""
    local_epochs: int = 1
    batch_size: int = 32
    learning_rate: float = 0.01
    fedprox_mu: float = 0.0
    global_control_variate: dict[str, torch.Tensor] = field(default_factory=dict)
    client_control_variate: dict[str, torch.Tensor] = field(default_factory=dict)
    # Privacy Engineering phase: set only when this run's privacy mode is
    # SAMPLE_LEVEL_DP or HYBRID_DP — see docs/sample-level-dp.md. The
    # worker must apply real Opacus-based DP-SGD using
    # sample_level_privacy and must never silently train non-privately
    # when sample_level_dp_active is true.
    sample_level_dp_active: bool = False
    sample_level_privacy: SampleLevelDPConfig | None = None
    # Secure Cohort Handshake and Signed Roster Runtime slice
    # (docs/secure-cohort-handshake-foundation.md), Work item 4: mirrors
    # fl.coordinator.v1.ClientTrainingTask.secure_aggregation exactly.
    # secure_aggregation_active defaults to False (CliBridgeCoordinatorClient
    # never populates this -- secure aggregation is gRPC-only, matching
    # rotate_signing_key/submit_security_events' existing precedent) --
    # callers must check that flag first, never infer activity from a
    # non-empty session_id alone.
    secure_aggregation: SecureAggregationTaskBindingFields = field(
        default_factory=SecureAggregationTaskBindingFields
    )
    # Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    # slice: mirrors fl.coordinator.v1.ClientTrainingTask.attempt (the
    # signed task's own lease-attempt counter) -- needed by
    # GrpcCoordinatorClient.submit_masked_update to bind the same
    # attempt number into the signed MaskedClientUpdate that
    # AcquireTask's response (and the coordinator's own lease record)
    # already carries. Not previously exposed on this dataclass because
    # nothing needed it before this slice.
    attempt: int = 0
    # Secure User-Level Differential Privacy Runtime slice, Work Area
    # I: the coordinator's own already-verified
    # secure_user_level_dp_configuration_hash for this signed task
    # (verify_coordinator_task already independently recomputed and
    # matched this value before acquire_task ever returns -- reused
    # here rather than recomputed a third time when building the
    # signed attestation).
    secure_user_level_dp_configuration_hash: str = ""


@dataclass(slots=True)
class SubmitOutcome:
    accepted: bool
    reason: str
    snapshot: RunSnapshot


@dataclass(slots=True, frozen=True)
class SecureAggregationKeyAdvertisementOutcome:
    """Result of GrpcCoordinatorClient.advertise_secure_aggregation_key --
    mirrors fl.coordinator.v1.AdvertiseSecureAggregationKeyResponse."""

    accepted: bool
    reason: str
    rejection_reason: str


@dataclass(slots=True, frozen=True)
class FrozenCohortRosterOutcome:
    """Result of GrpcCoordinatorClient.get_frozen_cohort_roster -- mirrors
    fl.coordinator.v1.GetFrozenCohortRosterResponse. `roster` is the raw
    generated fl.coordinator.v1.FrozenCohortRoster message (structurally
    typed as FrozenCohortRosterLike, matching
    key_advertisement.py's verify_frozen_cohort_roster signature) when
    available=True, else None."""

    available: bool
    reason: str
    roster: FrozenCohortRosterLike | None = None


@dataclass(slots=True, frozen=True)
class MaskedUpdateSubmissionOutcome:
    """Result of GrpcCoordinatorClient.submit_masked_update -- mirrors
    fl.coordinator.v1.SubmitMaskedClientUpdateResponse. Masked Update
    Runtime and No-Dropout Secure FedAvg Finalization slice."""

    accepted: bool
    reason: str
    rejection_reason: str


@dataclass(slots=True)
class RunSpec:
    """Everything needed to (re)create a run against the coordinator.

    The CLI bridge is stateless per call (see module docstring), so every
    request after the first re-sends this full spec — mirroring the
    documented recovery contract (create_run + restore_from_checkpoint)
    in cpp/coordinator/include/fl_coordinator/run_manager.hpp.
    """

    run_id: str
    algorithm: str
    weighting: str = "uniform"
    total_clients: int = 1
    target_clients_per_round: int = 1
    max_rounds: int = 1
    minimum_valid_results: int = 1
    client_ids: list[str] = field(default_factory=list)
    server_lr: float = 1.0
    beta1: float = 0.9
    beta2: float = 0.99
    tau: float = 1e-3
    contribution_cap: float = 1.0
    seed: int = 0
    task_lease_seconds: int = 60
    max_task_retries: int = 3
    local_epochs: int = 1
    batch_size: int = 32
    learning_rate: float = 0.01
    momentum: float = 0.0
    weight_decay: float = 0.0
    fedprox_mu: float = 0.0
    tensor_elements: int = 1
    # Algorithm Expansion phase: multi-tensor manifest
    # ("name:elements,name:elements", e.g. "backbone:64,head:12" for a
    # shared-backbone/personalized-head model) and aggregation manifest
    # (which of those names are aggregatable/personalized/frozen). Empty
    # tensor_specs falls back to the single "weight" tensor shape every
    # FedAvg/FedProx/SCAFFOLD test already uses. See
    # docs/aggregation-manifests.md.
    tensor_specs: str = ""
    shared_parameter_names: list[str] = field(default_factory=list)
    personalized_parameter_names: list[str] = field(default_factory=list)
    frozen_parameter_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PersonalizationMetricsSubmission:
    """What a worker reports alongside a training result for algorithms
    that produce a personalized model (Ditto, Per-FedAvg). Scalars only —
    never tensor values. Mirrors fl.coordinator.v1.PersonalizationMetricRecord."""

    global_local_accuracy: float
    personalized_local_accuracy: float
    global_local_loss: float = 0.0
    personalized_local_loss: float = 0.0
    sample_count: int = 0
    personalized_model_version: int = 0


@dataclass(slots=True)
class PersonalizationMetricRecord:
    client_id: str
    round_id: int
    algorithm: str
    global_local_accuracy: float
    personalized_local_accuracy: float
    personalized_improvement: float
    sample_count: int


class CoordinatorClient(Protocol):
    def create_run(self, spec: RunSpec, now: float) -> RunSnapshot: ...
    def start_run(
        self, spec: RunSpec, now: float, trace_id: str = ""
    ) -> RunSnapshot: ...
    def pause_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot: ...
    def resume_run(self, spec: RunSpec, now: float) -> RunSnapshot: ...
    def cancel_run(
        self, spec: RunSpec, now: float, reason: str = ""
    ) -> RunSnapshot: ...
    def get_run(self, spec: RunSpec, now: float) -> RunSnapshot: ...
    def register_worker(self, spec: RunSpec, worker_id: str, now: float) -> None: ...
    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask: ...
    def submit_result(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None = None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None = None,
        personalization_metrics: PersonalizationMetricsSubmission | None = None,
        sample_level_privacy: SampleLevelLedgerEntry | None = None,
        sample_privacy_decision: SampleBudgetDecision | None = None,
    ) -> SubmitOutcome: ...

    def get_personalization_summary(
        self, spec: RunSpec, now: float
    ) -> list[PersonalizationMetricRecord]: ...


def _spec_fields(spec: RunSpec) -> dict[str, str]:
    return {
        "run_id": spec.run_id,
        "algorithm": spec.algorithm,
        "weighting": spec.weighting,
        "total_clients": str(spec.total_clients),
        "target_clients_per_round": str(spec.target_clients_per_round),
        "max_rounds": str(spec.max_rounds),
        "minimum_valid_results": str(spec.minimum_valid_results),
        "client_ids": ",".join(spec.client_ids),
        "server_lr": repr(spec.server_lr),
        "beta1": repr(spec.beta1),
        "beta2": repr(spec.beta2),
        "tau": repr(spec.tau),
        "contribution_cap": repr(spec.contribution_cap),
        "seed": str(spec.seed),
        "task_lease_seconds": str(spec.task_lease_seconds),
        "max_task_retries": str(spec.max_task_retries),
        "local_epochs": str(spec.local_epochs),
        "batch_size": str(spec.batch_size),
        "learning_rate": repr(spec.learning_rate),
        "momentum": repr(spec.momentum),
        "weight_decay": repr(spec.weight_decay),
        "fedprox_mu": repr(spec.fedprox_mu),
        "tensor_elements": str(spec.tensor_elements),
        **({"tensor_specs": spec.tensor_specs} if spec.tensor_specs else {}),
        **(
            {"shared_parameter_names": ",".join(spec.shared_parameter_names)}
            if spec.shared_parameter_names
            else {}
        ),
        **(
            {
                "personalized_parameter_names": ",".join(
                    spec.personalized_parameter_names
                )
            }
            if spec.personalized_parameter_names
            else {}
        ),
        **(
            {"frozen_parameter_names": ",".join(spec.frozen_parameter_names)}
            if spec.frozen_parameter_names
            else {}
        ),
    }


def _parse_response(stdout: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields.setdefault(key, []).append(value)
    return fields


def _snapshot_from_fields(fields: dict[str, list[str]]) -> RunSnapshot:
    def first(key: str, default: str = "") -> str:
        values = fields.get(key)
        return values[0] if values else default

    return RunSnapshot(
        run_id=first("run_id"),
        state=first("state"),
        current_round=int(first("current_round", "0")),
        max_rounds=int(first("max_rounds", "0")),
        model_version=first("model_version"),
        algorithm=first("algorithm"),
        registered_workers=int(first("registered_workers", "0")),
        healthy_workers=int(first("healthy_workers", "0")),
    )


class CliBridgeCoordinatorClient:
    """Drives fl_coordinator_cli as a subprocess per call."""

    def __init__(self, cli_path: Path, state_dir: Path) -> None:
        self._cli_path = cli_path
        self._state_dir = state_dir

    def _run(
        self, command: str, request_fields: dict[str, str]
    ) -> dict[str, list[str]]:
        if not self._cli_path.exists():
            raise CoordinatorUnavailableError(
                f"fl_coordinator_cli not found at {self._cli_path}; build it with "
                "`cmake --build build/cpp-debug --target fl_coordinator_cli`."
            )
        payload = (
            "\n".join(f"{key}={value}" for key, value in request_fields.items()) + "\n"
        )
        result = subprocess.run(
            [str(self._cli_path), command, str(self._state_dir)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )
        fields = _parse_response(result.stdout)
        status = fields.get("status", [""])[0]
        if status != "ok":
            message = fields.get("message", ["coordinator CLI failed with no message"])[
                0
            ]
            raise CoordinatorRejectedError(message)
        return fields

    def create_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        return _snapshot_from_fields(self._run("create-run", fields))

    def start_run(self, spec: RunSpec, now: float, trace_id: str = "") -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["trace_id"] = trace_id
        return _snapshot_from_fields(self._run("start-run", fields))

    def pause_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["reason"] = reason
        return _snapshot_from_fields(self._run("pause-run", fields))

    def resume_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        return _snapshot_from_fields(self._run("resume-run", fields))

    def cancel_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["reason"] = reason
        return _snapshot_from_fields(self._run("cancel-run", fields))

    def get_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        return _snapshot_from_fields(self._run("get-run", fields))

    def register_worker(self, spec: RunSpec, worker_id: str, now: float) -> None:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["worker_id"] = worker_id
        self._run("register-worker", fields)

    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["worker_id"] = worker_id
        response = self._run("acquire-task", fields)
        has_task = response.get("has_task", ["0"])[0] == "1"
        if not has_task:
            return ClientTrainingTask(has_task=False)

        def first(key: str, default: str = "") -> str:
            values = response.get(key)
            return values[0] if values else default

        return ClientTrainingTask(
            has_task=True,
            task_id=first("task_id"),
            lease_id=first("lease_id"),
            client_id=first("client_id"),
            round_id=int(first("round_id", "0")),
            model_version=first("model_version"),
            algorithm=first("algorithm"),
            local_epochs=int(first("local_epochs", "1")),
            batch_size=int(first("batch_size", "32")),
            learning_rate=float(first("learning_rate", "0.01")),
            fedprox_mu=float(first("fedprox_mu", "0.0")),
            global_control_variate=decode_tensor_dict(
                response.get("global_control_variate", [])
            ),
            client_control_variate=decode_tensor_dict(
                response.get("client_control_variate", [])
            ),
        )

    def submit_result(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None = None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None = None,
        personalization_metrics: PersonalizationMetricsSubmission | None = None,
        sample_level_privacy: SampleLevelLedgerEntry | None = None,
        sample_privacy_decision: SampleBudgetDecision | None = None,
    ) -> SubmitOutcome:
        # sample_level_privacy/sample_privacy_decision are accepted for
        # CoordinatorClient Protocol conformance but not forwarded:
        # coordinator_cli.cpp's submit-result command is not
        # privacy-aware in this phase — the sample-level ledger is only
        # actually relayed over the live gRPC path (see
        # GrpcCoordinatorClient.submit_result). The CLI-bridge
        # integration tests exercise non-private algorithms only, so
        # this is a documented scope boundary, not a silent gap.
        del sample_level_privacy
        del sample_privacy_decision
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["worker_id"] = worker_id
        fields["task_id"] = task.task_id
        fields["lease_id"] = task.lease_id
        fields["round_id"] = str(task.round_id)
        fields["client_id"] = task.client_id
        fields["update_id"] = update_id
        fields["nonce"] = nonce
        fields["base_model_version"] = task.model_version
        fields["sample_count"] = str(sample_count)
        if len(delta) == 1:
            ((name, tensor),) = delta.items()
            fields["delta"] = encode_named_tensor(name, tensor)
        else:
            # Algorithm Expansion phase: shared-backbone submissions
            # carry more than one named tensor — read_request's map can
            # only hold one value per key, so this uses
            # delta_count/delta_0/.../delta_N
            # instead of repeating "delta=" (see coordinator_cli.cpp).
            fields["delta_count"] = str(len(delta))
            for index, (name, tensor) in enumerate(delta.items()):
                fields[f"delta_{index}"] = encode_named_tensor(name, tensor)
        if control_delta:
            ((cd_name, cd_tensor),) = control_delta.items()
            fields["control_delta"] = encode_named_tensor(cd_name, cd_tensor)
        if refreshed_client_control_variate:
            ((rc_name, rc_tensor),) = refreshed_client_control_variate.items()
            fields["refreshed_client_control_variate"] = encode_named_tensor(
                rc_name, rc_tensor
            )
        if personalization_metrics is not None:
            fields["personalization_global_accuracy"] = repr(
                personalization_metrics.global_local_accuracy
            )
            fields["personalization_personalized_accuracy"] = repr(
                personalization_metrics.personalized_local_accuracy
            )
            fields["personalization_global_loss"] = repr(
                personalization_metrics.global_local_loss
            )
            fields["personalization_personalized_loss"] = repr(
                personalization_metrics.personalized_local_loss
            )
            fields["personalization_sample_count"] = str(
                personalization_metrics.sample_count
            )
            fields["personalization_model_version"] = str(
                personalization_metrics.personalized_model_version
            )

        response = self._run("submit-result", fields)
        accepted = response.get("accepted", ["0"])[0] == "1"
        reason = response.get("reason", [""])[0]
        return SubmitOutcome(
            accepted=accepted, reason=reason, snapshot=_snapshot_from_fields(response)
        )

    def get_personalization_summary(
        self, spec: RunSpec, now: float
    ) -> list[PersonalizationMetricRecord]:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        response = self._run("get-personalization-summary", fields)
        client_ids = response.get("client_id", [])
        round_ids = response.get("round_id", [])
        algorithms = response.get("algorithm", [])
        global_accuracies = response.get("global_local_accuracy", [])
        personalized_accuracies = response.get("personalized_local_accuracy", [])
        improvements = response.get("personalized_improvement", [])
        sample_counts = response.get("sample_count", [])
        return [
            PersonalizationMetricRecord(
                client_id=client_ids[i],
                round_id=int(round_ids[i]),
                algorithm=algorithms[i],
                global_local_accuracy=float(global_accuracies[i]),
                personalized_local_accuracy=float(personalized_accuracies[i]),
                personalized_improvement=float(improvements[i]),
                sample_count=int(sample_counts[i]),
            )
            for i in range(len(client_ids))
        ]


def _parse_tensor_specs(spec: RunSpec) -> list[tuple[str, int]]:
    """Mirrors coordinator_cli.cpp's parse_tensor_specs: comma-separated
    "name:elements" pairs, falling back to a single "weight" tensor of
    ``tensor_elements`` elements when unset — see docs/aggregation-manifests.md.
    """
    if not spec.tensor_specs:
        return [("weight", spec.tensor_elements)]
    tensors: list[tuple[str, int]] = []
    for entry in spec.tensor_specs.split(","):
        name, _, elements = entry.partition(":")
        tensors.append((name, int(elements)))
    return tensors


def _coordinator_task_rejection_event_type(
    reason: CoordinatorTaskRejectionReason,
) -> str:
    """Maps a CoordinatorTaskRejectionReason onto the closest-matching
    security event type -- Security Events, Metrics, and Durable Audit
    Journal slice."""
    if reason in (
        CoordinatorTaskRejectionReason.DUPLICATE_NONCE,
        CoordinatorTaskRejectionReason.DUPLICATE_OR_LOWER_SEQUENCE,
    ):
        return _security_event.EVENT_COORDINATOR_TASK_REPLAY_REJECTED
    if reason == CoordinatorTaskRejectionReason.DUPLICATE_TASK_EXECUTION:
        return _security_event.EVENT_DUPLICATE_TASK_EXECUTION_BLOCKED
    return _security_event.EVENT_COORDINATOR_TASK_REJECTED


# Prefixes/values of SubmitClientResultResponse.rejection_code
# (coordinator_service.cpp) that indicate the *privacy record* embedded
# in the submission was the actual cause of rejection, as opposed to the
# client-result envelope itself -- see coordinator_service.cpp's
# rejection_code assignments around its privacy-record verification
# block. Used to route the worker's own local event emission to
# EVENT_PRIVACY_RECORD_REJECTED instead of EVENT_CLIENT_RESULT_REJECTED
# so the Security Overview's separate "privacy record authenticity"
# tile reflects real rejections, not just client-result ones.
_PRIVACY_REJECTION_CODES = frozenset(
    {
        "privacy_record_key_mismatch",
        "privacy_payload_hash_mismatch",
        "privacy_record_binding_mismatch",
        "budget_decision_contradiction",
        "privacy_record_missing",
    }
)


def _is_privacy_rejection_code(rejection_code: str) -> bool:
    return rejection_code in _PRIVACY_REJECTION_CODES or rejection_code.startswith(
        "privacy_"
    )


class GrpcCoordinatorClient:
    """Real grpcio client for a long-lived C++ coordinator gRPC server.

    Implements every CoordinatorClient method for real — see
    docs/create-run-wire-mapping.md for how this maps onto
    CreateRunRequest's fields (mirroring the same mapping validated by
    Go's GrpcClient.CreateRun and the C++ coordinator_service_test.cpp
    integration test). Unlike CliBridgeCoordinatorClient (which resends
    the full RunSpec on every call because the CLI subprocess model is
    stateless per invocation — see RunSpec's docstring), most methods
    here only need spec.run_id: the persistent gRPC server already holds
    the run's configuration from create_run.
    """

    def __init__(
        self,
        address: str,
        *,
        insecure: bool = True,
        tls_config: WorkerTLSConfig | None = None,
        signing_identity: WorkerSigningIdentity | None = None,
        sequence_state_path: str | None = None,
        trusted_coordinator_keys_path: str | None = None,
        coordinator_task_replay_store_path: str | None = None,
        accepted_task_journal_path: str | None = None,
        security_event_journal_path: str | None = None,
    ) -> None:
        # Signed Client Results and Worker Lifecycle Enforcement slice
        # (docs/signed-client-results.md): signing_identity is optional
        # (None by default) so every existing call site keeps compiling
        # and behaving exactly as before -- register_worker/submit_result
        # both stay fully unsigned when it is None, same
        # optional-by-default-for-backward-compatibility convention
        # already used by CoordinatorServiceImpl's identity_registry/
        # replay_store parameters on the C++ side. When provided, this
        # client signs every RegisterWorker capability statement and
        # every SubmitClientResult envelope for real -- see
        # register_worker/submit_result below.
        self._signing_identity = signing_identity
        self._sequence_store: SequenceStateStore | None = None
        if signing_identity is not None:
            path = (
                sequence_state_path
                or f".fl_worker_sequence_state.{signing_identity.worker_id}.json"
            )
            self._sequence_store = SequenceStateStore(path)

        # Coordinator-Signed Tasks slice (docs/signed-coordinator-tasks.md):
        # trusted_coordinator_keys_path is optional (None by default),
        # same backward-compatible convention as signing_identity above.
        # When None, acquire_task never attempts verification -- if the
        # coordinator nonetheless sends a signed_task, that is treated as
        # a hard misconfiguration (a coordinator that signs tasks needs
        # its workers configured to verify them; silently accepting an
        # unverified signed task would defeat the entire feature) rather
        # than silently ignored -- see acquire_task below.
        self._trusted_coordinator_keys_path = trusted_coordinator_keys_path
        self._coordinator_task_replay_store: CoordinatorTaskReplayStore | None = None
        self._accepted_task_journal: AcceptedTaskJournal | None = None
        # Security Events, Metrics, and Durable Audit Journal slice
        # (docs/security-events.md): optional, None by default, same
        # backward-compatible convention as every store above. Local to
        # this worker process only -- not shipped to the coordinator/Go
        # this slice (see docs/known-limitations.md).
        self._security_event_journal: SecurityEventJournal | None = None
        # Web Security Center, Event Centralization, and Security CI
        # slice (docs/security-event-centralization.md): the queue wraps
        # the SAME journal instance _emit_security_event already writes
        # to -- there is no second, parallel store. None whenever the
        # journal itself is None, same optional-by-default convention as
        # every store above.
        self._security_event_queue: WorkerSecurityEventQueue | None = None
        if security_event_journal_path is not None:
            self._security_event_journal = SecurityEventJournal(
                security_event_journal_path
            )
            self._security_event_queue = WorkerSecurityEventQueue(
                self._security_event_journal
            )
        # Security Administration slice, Work Package F
        # (docs/trusted-coordinator-key-bundle.md): a stateful reloader
        # replaces the previous "read the file fresh on every
        # acquire_task call" approach -- that older approach had no way
        # to reject a rollback (an older bundle_version silently
        # replacing a newer one) or to keep the previous valid bundle
        # when a reload fails; the reloader does both. Constructed once
        # here (a failure to load the *initial* bundle is still fatal,
        # matching the pre-existing "no trust-on-first-use" behavior --
        # a worker that cannot load a real bundle cannot verify
        # anything and should not silently proceed).
        self._trust_bundle_reloader: TrustedCoordinatorKeyBundleReloader | None = None
        if trusted_coordinator_keys_path is not None:
            worker_id_for_path = (
                signing_identity.worker_id if signing_identity is not None else address
            )
            replay_path = (
                coordinator_task_replay_store_path
                or f".fl_coordinator_task_replay.{worker_id_for_path}.json"
            )
            journal_path = (
                accepted_task_journal_path
                or f".fl_accepted_task_journal.{worker_id_for_path}.json"
            )
            self._coordinator_task_replay_store = CoordinatorTaskReplayStore(
                replay_path
            )
            self._accepted_task_journal = AcceptedTaskJournal(journal_path)
            self._trust_bundle_reloader = TrustedCoordinatorKeyBundleReloader(
                trusted_coordinator_keys_path
            )

        from fl_platform.rpc import ensure_generated_on_path

        ensure_generated_on_path()
        # `coordinator`/`worker`/`experiment` are structurally unresolvable
        # by static analysis: they only become importable at runtime after
        # ensure_generated_on_path() inserts the gitignored generated/
        # directory onto sys.path above — mypy cannot see that. Narrowly
        # ignored rather than disabling import checking broadly. `grpc`
        # itself needs no inline ignore: it's covered by pyproject.toml's
        # `[[tool.mypy.overrides]]` for module "grpc.*".
        import grpc  # noqa: PLC0415 - deferred: only needed if this backend is used
        from coordinator import (  # type: ignore[import-not-found]  # noqa: PLC0415
            coordinator_pb2,
            coordinator_pb2_grpc,
        )
        from experiment import (  # type: ignore[import-not-found]  # noqa: PLC0415
            experiment_pb2,
        )
        from privacy import (  # type: ignore[import-not-found]  # noqa: PLC0415
            privacy_pb2,
        )
        from worker import worker_pb2  # type: ignore[import-not-found]  # noqa: PLC0415

        self._pb2 = coordinator_pb2
        self._experiment_pb2 = experiment_pb2
        self._worker_pb2 = worker_pb2
        self._privacy_pb2 = privacy_pb2
        # Secure Transport and Worker Identity Hardening slice
        # (docs/mtls.md): insecure=False now builds a real TLS/mTLS
        # channel via fl_platform.security.transport, which has been
        # exercised against a real local gRPC server secured with
        # certificates generated for the test — see
        # python/tests/test_worker_transport.py. This is the transport
        # *layer* proven real; the coordinator domain RPCs
        # (CreateRun/AcquireTask/etc.) below still have not been
        # exercised against a live C++ coordinator server in this
        # environment, for the pre-existing reason stated in this
        # class's docstring (no local gRPC C++ toolchain).
        if insecure:
            self._channel = grpc.insecure_channel(address)
        else:
            if tls_config is None:
                raise transport.TransportConfigurationError(
                    "insecure=False requires tls_config to be set; insecure transport "
                    "requires insecure=True explicitly — see docs/mtls.md"
                )
            self._channel = transport.build_secure_channel(address, tls_config)
        self._stub = coordinator_pb2_grpc.CoordinatorServiceStub(self._channel)

    def _grpc_call(self, rpc_callable: Any, request: Any) -> Any:
        """Security Runtime Completion and Release Evidence slice, Work
        Package B: wraps a single unary gRPC call, translating
        grpc.RpcError into this module's typed CoordinatorClient
        exception hierarchy (CoordinatorUnavailableError /
        CoordinatorRejectedError) -- WorkerService.run()'s existing
        retry/error-handling logic (service.py) only ever catches those
        two types, never a raw grpc.RpcError.

        Before this slice, every self._stub.XxxRPC(request) call site in
        this class let a raw grpc.RpcError propagate uncaught out of
        register_worker/acquire_task/submit_result/etc -- undetected
        because, per this class's own module docstring, GrpcCoordinatorClient
        "has not been exercised against a real server in this
        environment" before this slice's live Docker validation (see
        docs/security-event-centralization.md). A worker process using
        the real gRPC client would have crashed uncaught on the very
        first transient failure (e.g. the coordinator briefly
        unavailable during startup ordering) instead of retrying, as
        WorkerService's tests (against CliBridgeCoordinatorClient /
        mocks only) already assume.

        UNAVAILABLE/DEADLINE_EXCEEDED map to the retryable
        CoordinatorUnavailableError; every other gRPC status
        (PERMISSION_DENIED, INVALID_ARGUMENT, FAILED_PRECONDITION, ...)
        maps to the non-retryable CoordinatorRejectedError.
        """
        import grpc  # noqa: PLC0415 - see __init__'s identical deferred-import rationale

        try:
            return rpc_callable(request)
        except grpc.RpcError as error:
            code = error.code() if hasattr(error, "code") else None
            detail = error.details() if hasattr(error, "details") else str(error)
            if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                raise CoordinatorUnavailableError(str(detail)) from error
            raise CoordinatorRejectedError(str(detail)) from error

    @property
    def accepted_task_journal(self) -> AcceptedTaskJournal | None:
        """None unless trusted_coordinator_keys_path was configured --
        WorkerService reads this (via getattr, since
        CliBridgeCoordinatorClient has no such attribute) to drive its
        own PREPARING/TRAINING/.../COMPLETED transitions on the same
        journal acquire_task uses for ACCEPTED/duplicate-execution
        detection."""
        return self._accepted_task_journal

    def _emit_security_event(
        self,
        *,
        event_type: str,
        subject_type: str,
        outcome: str,
        worker_id: str = "",
        task_id: str = "",
        reason_code: str = "",
        safe_signing_key_id: str = "",
    ) -> None:
        """No-op unless security_event_journal_path was configured.
        Always writes to this worker's own local journal first;
        centralization to the coordinator (see
        docs/security-event-centralization.md) happens asynchronously,
        via WorkerSecurityEventQueue selecting from this same journal
        and submit_security_events flushing it -- this method itself
        never talks to the coordinator directly."""
        if self._security_event_journal is None:
            return
        event = _security_event.SecurityEvent(
            source_service="python-worker",
            source_component="coordinator_client",
            event_type=event_type,
            severity=_security_event.default_severity(event_type),
            actor_type=_security_event.ACTOR_TYPE_WORKER,
            safe_actor_id=worker_id,
            subject_type=subject_type,
            safe_subject_id=task_id or worker_id,
            worker_id=worker_id,
            task_id=task_id,
            safe_signing_key_id=safe_signing_key_id,
            outcome=outcome,
            reason_code=reason_code,
        )
        self._security_event_journal.emit(event)
        try:
            from fl_platform.security.metrics import record_security_event

            record_security_event(event)
        except ImportError:
            pass  # prometheus_client is an optional dependency for the worker

    def reload_trusted_coordinator_keys(self) -> BundleReloadResult | None:
        """Security Administration slice, Work Package F: an explicit
        worker-control-signal reload trigger (the specification's
        third supported mechanism, alongside file-modification
        detection and periodic bounded reload -- neither of which is
        wired to a background scheduler this pass; see
        docs/trusted-coordinator-key-bundle.md's "What is deferred"
        section). Returns None if no trusted_coordinator_keys_path was
        configured. Logs the outcome at INFO (a real, accepted change),
        WARNING (rejected -- corrupt file, rollback attempt, or
        duplicate ACTIVE keys; the previous valid bundle remains in
        effect), or DEBUG (accepted, nothing changed)."""
        if self._trust_bundle_reloader is None:
            return None
        result = self._trust_bundle_reloader.reload()
        if not result.accepted:
            logger.warning(
                "trusted-coordinator-key bundle reload rejected: %s", result.reason
            )
        elif result.changed:
            logger.info(
                "trusted-coordinator-key bundle reloaded: now bundle_version=%d",
                self._trust_bundle_reloader.current_bundle().bundle_version,
            )
        else:
            logger.debug("trusted-coordinator-key bundle reload: no change")
        return result

    def health(self, trace_id: str = "") -> str:
        response = self._grpc_call(
            self._stub.Health, self._pb2.HealthRequest(trace_id=trace_id)
        )
        return str(response.status)

    def _run_snapshot(
        self,
        run_id: str,
        state: str,
        current_round: int = 0,
        max_rounds: int = 0,
        model_version: str = "",
        algorithm: str = "",
        registered_workers: int = 0,
        healthy_workers: int = 0,
    ) -> RunSnapshot:
        return RunSnapshot(
            run_id=run_id,
            state=state,
            current_round=current_round,
            max_rounds=max_rounds,
            model_version=model_version,
            algorithm=algorithm,
            registered_workers=registered_workers,
            healthy_workers=healthy_workers,
        )

    def create_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        del now  # the persistent gRPC server timestamps server-side
        model_manifest = self._pb2.ModelManifest(
            model_id="toy",
            model_version="v0",
            tensors=[
                self._worker_pb2.TensorManifest(name=name, shape=[elements])
                for name, elements in _parse_tensor_specs(spec)
            ],
            aggregation_manifest=self._pb2.AggregationManifest(
                shared_parameter_names=spec.shared_parameter_names,
                personalized_parameter_names=spec.personalized_parameter_names,
                frozen_parameter_names=spec.frozen_parameter_names,
            ),
        )
        request = self._pb2.CreateRunRequest(
            config=self._experiment_pb2.RunConfiguration(
                run_id=spec.run_id, rounds=spec.max_rounds
            ),
            optimizer=self._pb2.OptimizerConfig(
                algorithm=spec.algorithm,
                weighting=spec.weighting,
                server_lr=spec.server_lr,
                beta1=spec.beta1,
                beta2=spec.beta2,
                tau=spec.tau,
                contribution_cap=spec.contribution_cap,
            ),
            target_clients_per_round=spec.target_clients_per_round,
            total_clients=spec.total_clients,
            max_rounds=spec.max_rounds,
            minimum_valid_results=spec.minimum_valid_results,
            client_selection_seed=spec.seed,
            client_ids=spec.client_ids,
            local_epochs=spec.local_epochs,
            batch_size=spec.batch_size,
            learning_rate=spec.learning_rate,
            momentum=spec.momentum,
            weight_decay=spec.weight_decay,
            fedprox_mu=spec.fedprox_mu,
            task_lease_seconds=spec.task_lease_seconds,
            max_task_retries=spec.max_task_retries,
            model_manifest=model_manifest,
        )
        response = self._grpc_call(self._stub.CreateRun, request)
        return self._run_snapshot(response.run_id, response.state)

    def start_run(self, spec: RunSpec, now: float, trace_id: str = "") -> RunSnapshot:
        del now
        response = self._grpc_call(
            self._stub.StartRun,
            self._pb2.StartRunRequest(run_id=spec.run_id, trace_id=trace_id),
        )
        return self._run_snapshot(
            response.run_id,
            response.state,
            response.current_round,
            model_version=response.model_version,
        )

    def pause_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        del now
        response = self._grpc_call(
            self._stub.PauseRun,
            self._pb2.PauseRunRequest(run_id=spec.run_id, reason=reason),
        )
        return self._run_snapshot(
            response.run_id,
            response.state,
            response.current_round,
            model_version=response.model_version,
        )

    def resume_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        del now
        response = self._grpc_call(
            self._stub.ResumeRun, self._pb2.ResumeRunRequest(run_id=spec.run_id)
        )
        return self._run_snapshot(
            response.run_id,
            response.state,
            response.current_round,
            model_version=response.model_version,
        )

    def cancel_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        del now
        response = self._grpc_call(
            self._stub.CancelRun,
            self._pb2.CancelRunRequest(run_id=spec.run_id, reason=reason),
        )
        return self._run_snapshot(
            response.run_id,
            response.state,
            response.current_round,
            model_version=response.model_version,
        )

    def get_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        del now
        response = self._grpc_call(
            self._stub.GetRun, self._pb2.GetRunRequest(run_id=spec.run_id)
        )
        return self._run_snapshot(
            response.run_id,
            response.state,
            response.current_round,
            response.max_rounds,
            response.model_version,
            response.algorithm,
            response.registered_workers,
            response.healthy_workers,
        )

    def register_worker(self, spec: RunSpec, worker_id: str, now: float) -> None:
        # RegisterWorker is run-agnostic: a worker registers with the
        # coordinator process, not a specific run.
        del spec, now
        # Privacy Engineering phase (docs/worker-privacy-capabilities.md):
        # supports_sample_level_dp reflects whether Opacus is genuinely
        # importable in this process, probed truthfully via
        # importlib.metadata rather than hardcoded — the coordinator's
        # compatible-worker-only task assignment (RunInstance::acquire_task)
        # depends on this being accurate, not optimistic.
        #
        # supports_secure_random: closure-gate work for the Secure
        # Transport and Worker Identity Hardening slice
        # (docs/secure-random-runtime.md) — computed via
        # worker_reports_secure_random_support(), which truthfully
        # probes whether Opacus's own secure_mode (torchcsprng-backed
        # DP-SGD noise) can actually be enabled in this process, not
        # whether the process can merely access an OS CSPRNG at all
        # (it always can, via stdlib `secrets`, which is a different and
        # irrelevant question — see fl_platform.privacy.secure_random's
        # module docstring). Real and dynamic, not hardcoded: True only
        # when torchcsprng is actually installed.
        installed, version = opacus_capabilities()
        supported_accountants: list[int] = []
        if installed:
            for name in SUPPORTED_ACCOUNTANTS:
                supported_accountants.append(_accountant_type_to_wire(name))
        privacy_capabilities = self._privacy_pb2.WorkerPrivacyCapabilities(
            supports_sample_level_dp=installed,
            opacus_version=version,
            supported_accountants=supported_accountants,
            supports_secure_random=worker_reports_secure_random_support(),
        )
        request = self._worker_pb2.RegisterWorkerRequest(
            worker_id=worker_id,
            capability=self._worker_pb2.WorkerCapability(
                device="cpu", privacy=privacy_capabilities
            ),
        )
        if self._signing_identity is not None:
            # Closes the gap flagged in docs/rpc-security-policy.md /
            # docs/message-authenticity-report.md: previously this
            # client sent a completely unsigned RegisterWorkerRequest
            # even though the coordinator has verified signed capability
            # statements since the prior slice — see
            # docs/signed-capabilities.md.
            request.signed_capability.CopyFrom(
                self._build_signed_capability_statement(
                    worker_id, installed, version, supported_accountants
                )
            )
        try:
            self._grpc_call(self._stub.RegisterWorker, request)
        except CoordinatorRejectedError as error:
            # Work Package O critical-event coverage: registration
            # rejection (e.g. a certificate-bound worker_id mismatch, or
            # a revoked worker_id attempting to re-register) is exactly
            # the kind of security-relevant failure this worker's local
            # journal -- and, once centralized, the coordinator's own
            # journal -- must not silently drop.
            self._emit_security_event(
                event_type=_security_event.EVENT_WORKER_REGISTRATION_REJECTED,
                subject_type=_security_event.SUBJECT_TYPE_WORKER_IDENTITY,
                worker_id=worker_id,
                outcome=_security_event.OUTCOME_REJECTED,
                reason_code=str(error)[:120],
            )
            raise
        # Work Package O/N: a successful registration is itself worth a
        # LIVE_REQUIRED event -- previously nothing was ever emitted to
        # this worker's local journal on the happy path, so there was
        # never anything for the event-centralization pipeline (added
        # this slice, see docs/security-event-centralization.md) to
        # actually relay for a healthy worker.
        self._emit_security_event(
            event_type=_security_event.EVENT_WORKER_REGISTERED,
            subject_type=_security_event.SUBJECT_TYPE_WORKER_IDENTITY,
            worker_id=worker_id,
            outcome=_security_event.OUTCOME_ACCEPTED,
            safe_signing_key_id=(
                self._signing_identity.key_id
                if self._signing_identity is not None
                else ""
            ),
        )

    def _build_signed_capability_statement(
        self,
        worker_id: str,
        opacus_installed: bool,
        opacus_version: str,
        supported_accountants_wire: list[int],
    ) -> object:
        assert (
            self._signing_identity is not None
        )  # narrows Optional for mypy; caller already checked
        identity = self._signing_identity
        issued_at = security_now()
        accountant_names = [
            name
            for name in SUPPORTED_ACCOUNTANTS
            if _accountant_type_to_wire(name) in supported_accountants_wire
        ]
        payload = CapabilityStatementPayload(
            worker_id=worker_id,
            software_version="",
            build_id="",
            supported_algorithms=(),
            supported_privacy_modes=("sample_level",) if opacus_installed else (),
            opacus_version=opacus_version,
            secure_random_available=worker_reports_secure_random_support(),
            supported_accountants=tuple(accountant_names),
            supported_clipping_modes=(),
            supported_models=(),
            supported_model_schema_hashes=(),
            maximum_task_bytes=0,
            maximum_private_batch_size=0,
            cpu_count=0,
            gpu_available=False,
            gpu_count=0,
            signing_key_id=identity.key_id,
            issued_at=issued_at,
            expires_at=issued_at + 3600.0,
        )
        signed = sign_capability_statement(payload, identity)
        return self._worker_pb2.SignedCapabilityStatement(
            schema_version=signed.payload["schema_version"],
            worker_id=signed.payload["worker_id"],
            software_version=signed.payload["software_version"],
            build_id=signed.payload["build_id"],
            supported_algorithms=signed.payload["supported_algorithms"],
            supported_privacy_modes=signed.payload["supported_privacy_modes"],
            opacus_version=signed.payload["opacus_version"],
            secure_random_available=signed.payload["secure_random_available"],
            supported_accountants=signed.payload["supported_accountants"],
            supported_clipping_modes=signed.payload["supported_clipping_modes"],
            supported_models=signed.payload["supported_models"],
            supported_model_schema_hashes=signed.payload[
                "supported_model_schema_hashes"
            ],
            maximum_task_bytes=signed.payload["maximum_task_bytes"],
            maximum_private_batch_size=signed.payload["maximum_private_batch_size"],
            cpu_count=signed.payload["cpu_count"],
            gpu_available=signed.payload["gpu_available"],
            gpu_count=signed.payload["gpu_count"],
            issued_at=signed.payload["issued_at"],
            expires_at=signed.payload["expires_at"],
            nonce=signed.payload["nonce"],
            signing_key_id=signed.payload["signing_key_id"],
            signing_public_key=identity.public_key_hex(),
            payload_hash=signed.payload_hash,
            signature=signed.signature,
        )

    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask:
        del now
        response = self._grpc_call(
            self._stub.AcquireTask,
            self._pb2.AcquireTaskRequest(worker_id=worker_id, run_id=spec.run_id),
        )
        if not response.task_available:
            return ClientTrainingTask(has_task=False)
        task = response.task
        sample_level_privacy = None
        if response.sample_level_dp_active:
            wire_privacy = response.sample_level_privacy
            sample_level_privacy = SampleLevelDPConfig(
                noise_multiplier=wire_privacy.noise_multiplier,
                max_grad_norm=wire_privacy.max_grad_norm,
                target_delta=wire_privacy.target_delta,
                accountant=_accountant_type_from_wire(wire_privacy.accountant),
                poisson_sampling=wire_privacy.poisson_sampling,
                epsilon_budget=wire_privacy.epsilon_budget,
            )

        # Populated only inside the signed_task branch below -- an
        # unsigned/legacy task (no signed_task on the wire) has no
        # verified secure_user_level_dp_configuration_hash to reuse, so
        # this stays "" for that path (secure user-level DP always
        # requires a signed task in practice, since AcquireTask never
        # marks secure_user_level_dp_active without also signing the
        # task -- but this default keeps ClientTrainingTask construction
        # below well-defined regardless).
        secure_user_level_dp_configuration_hash = ""

        # Coordinator-Signed Tasks slice (docs/signed-coordinator-tasks.md):
        # every check below runs before this method returns a task to its
        # caller (WorkerService.run), i.e. before any model build, dataset
        # access, or Opacus/CUDA initialization happens -- a rejection
        # raises CoordinatorTaskRejectedError and no task is ever returned.
        if response.HasField("signed_task"):
            if self._trusted_coordinator_keys_path is None:
                raise CoordinatorTaskRejectedError(
                    CoordinatorTaskRejectionReason.UNKNOWN_SIGNING_KEY,
                    "coordinator sent a signed_task but this worker has no "
                    "trusted_coordinator_keys_path configured -- refusing to accept "
                    "an unverifiable signed task rather than silently treating it as "
                    "unsigned",
                )
            assert self._trust_bundle_reloader is not None  # narrows Optional for mypy
            # Reload before verifying (Work Package F: "reload before
            # rejecting an unknown but structurally valid coordinator
            # key") -- a rotation the coordinator performed since this
            # client last checked is picked up here; a failed reload
            # (corrupt file, rollback attempt) is logged and simply
            # keeps using whatever bundle was already valid.
            self.reload_trusted_coordinator_keys()
            trusted_keys = self._trust_bundle_reloader.current_keys()
            signed = response.signed_task
            secure_user_level_dp_configuration_hash = (
                signed.secure_user_level_dp_configuration_hash
            )
            aggregation_fields = AggregationManifestFields(
                shared_parameter_names=tuple(
                    response.aggregation_manifest.shared_parameter_names
                ),
                personalized_parameter_names=tuple(
                    response.aggregation_manifest.personalized_parameter_names
                ),
                frozen_parameter_names=tuple(
                    response.aggregation_manifest.frozen_parameter_names
                ),
                schema_hash=response.aggregation_manifest.schema_hash,
            )
            task_fields = TaskConfigurationFields(
                run_id=task.run_id,
                round_id=task.round_id,
                client_id=task.client_id,
                model_version=task.model_version,
                algorithm=task.algorithm,
                dataset_reference=task.dataset_reference,
                model_manifest=tuple(
                    TensorDescriptor(
                        name=tensor.name,
                        shape=tuple(tensor.shape),
                        dtype=tensor.dtype,
                        byte_length=tensor.byte_length,
                        checksum=tensor.checksum,
                    )
                    for tensor in task.model_manifest
                ),
                task_id=response.task_id,
                lease_id=response.lease_id,
                lease_expires_at=response.lease_expires_at,
                attempt=response.attempt,
                local_epochs=response.local_epochs,
                batch_size=response.batch_size,
                learning_rate=response.learning_rate,
                momentum=response.momentum,
                weight_decay=response.weight_decay,
                fedprox_mu=response.fedprox_mu,
                aggregation_manifest=aggregation_fields,
                sample_level_dp_active=response.sample_level_dp_active,
                sample_level_privacy=(
                    SampleLevelPrivacyFields(
                        noise_multiplier=response.sample_level_privacy.noise_multiplier,
                        max_grad_norm=response.sample_level_privacy.max_grad_norm,
                        target_delta=response.sample_level_privacy.target_delta,
                        accountant=response.sample_level_privacy.accountant,
                        poisson_sampling=response.sample_level_privacy.poisson_sampling,
                        epsilon_budget=response.sample_level_privacy.epsilon_budget,
                        sample_budget_policy=response.sample_level_privacy.sample_budget_policy,
                    )
                    if response.sample_level_dp_active
                    else None
                ),
                secure_aggregation=SecureAggregationTaskBindingFields(
                    secure_aggregation_active=response.secure_aggregation.secure_aggregation_active,
                    provider=response.secure_aggregation.provider,
                    protocol_version=response.secure_aggregation.protocol_version,
                    session_id=response.secure_aggregation.session_id,
                    session_configuration_hash=response.secure_aggregation.session_configuration_hash,
                    key_advertisement_deadline_unix_s=response.secure_aggregation.key_advertisement_deadline_unix_s,
                    minimum_cohort_size=response.secure_aggregation.minimum_cohort_size,
                    masked_update_deadline_unix_s=response.secure_aggregation.masked_update_deadline_unix_s,
                    session_expiry_unix_s=response.secure_aggregation.session_expiry_unix_s,
                    max_absolute_update_bound=response.secure_aggregation.max_absolute_update_bound,
                    max_client_weight=response.secure_aggregation.max_client_weight,
                    max_aggregate_bound=response.secure_aggregation.max_aggregate_bound,
                    tensor_chunk_size=response.secure_aggregation.tensor_chunk_size,
                    privacy_mode_compatible=response.secure_aggregation.privacy_mode_compatible,
                    privacy_incompatibility_reason=response.secure_aggregation.privacy_incompatibility_reason,
                    secure_user_level_dp_active=(
                        response.secure_aggregation.secure_user_level_dp_active
                    ),
                    secure_user_level_adjacency_model=(
                        response.secure_aggregation.secure_user_level_adjacency_model
                    ),
                    secure_user_level_clip_norm=response.secure_aggregation.secure_user_level_clip_norm,
                    secure_user_level_quantization_margin=(
                        response.secure_aggregation.secure_user_level_quantization_margin
                    ),
                    secure_user_level_effective_sensitivity=(
                        response.secure_aggregation.secure_user_level_effective_sensitivity
                    ),
                    secure_user_level_noise_multiplier=(
                        response.secure_aggregation.secure_user_level_noise_multiplier
                    ),
                    secure_user_level_target_delta=(
                        response.secure_aggregation.secure_user_level_target_delta
                    ),
                    secure_user_level_max_epsilon=response.secure_aggregation.secure_user_level_max_epsilon,
                    secure_user_level_fixed_weight=(
                        response.secure_aggregation.secure_user_level_fixed_weight
                    ),
                    secure_user_level_sampling_assumption=(
                        response.secure_aggregation.secure_user_level_sampling_assumption
                    ),
                ),
            )
            signed_fields = SignedCoordinatorTaskFields(
                coordinator_signing_key_id=signed.coordinator_signing_key_id,
                worker_id=signed.worker_id,
                task_id=signed.task_id,
                lease_id=signed.lease_id,
                attempt=signed.attempt,
                issued_at=signed.issued_at,
                expires_at=signed.expires_at,
                nonce=signed.nonce,
                sequence_number=signed.sequence_number,
                training_configuration_hash=signed.training_configuration_hash,
                model_configuration_hash=signed.model_configuration_hash,
                dataset_partition_hash=signed.dataset_partition_hash,
                privacy_configuration_hash=signed.privacy_configuration_hash,
                personalization_configuration_hash=signed.personalization_configuration_hash,
                task_payload_hash=signed.task_payload_hash,
                secure_aggregation_configuration_hash=signed.secure_aggregation_configuration_hash,
                secure_user_level_dp_configuration_hash=(
                    signed.secure_user_level_dp_configuration_hash
                ),
                schema_version=signed.schema_version,
            )
            # Both narrow Optional for mypy: guaranteed non-None together
            # whenever trusted_coordinator_keys_path was set in __init__
            # (the only path that reaches this branch).
            assert self._coordinator_task_replay_store is not None
            assert self._accepted_task_journal is not None
            try:
                verify_coordinator_task(
                    CoordinatorTaskVerificationParams(
                        signed_fields=signed_fields,
                        signature_hex=signed.signature,
                        task_fields=task_fields,
                        worker_id=worker_id,
                        now=security_now(),
                    ),
                    trusted_keys,
                    self._coordinator_task_replay_store,
                )
                try:
                    self._accepted_task_journal.record_accepted(
                        task_id=response.task_id,
                        lease_id=response.lease_id,
                        attempt=response.attempt,
                        worker_id=worker_id,
                        coordinator_signing_key_id=signed.coordinator_signing_key_id,
                        now=security_now(),
                    )
                except DuplicateTaskExecutionError as error:
                    raise CoordinatorTaskRejectedError(
                        CoordinatorTaskRejectionReason.DUPLICATE_TASK_EXECUTION,
                        str(error),
                    ) from error
            except CoordinatorTaskRejectedError as error:
                self._emit_security_event(
                    event_type=_coordinator_task_rejection_event_type(error.reason),
                    subject_type=_security_event.SUBJECT_TYPE_TRAINING_TASK,
                    worker_id=worker_id,
                    task_id=response.task_id,
                    outcome=_security_event.OUTCOME_REJECTED,
                    reason_code=error.reason.value,
                    safe_signing_key_id=signed.coordinator_signing_key_id,
                )
                raise
            self._emit_security_event(
                event_type=_security_event.EVENT_COORDINATOR_TASK_VERIFIED,
                subject_type=_security_event.SUBJECT_TYPE_TRAINING_TASK,
                worker_id=worker_id,
                task_id=response.task_id,
                outcome=_security_event.OUTCOME_ACCEPTED,
                safe_signing_key_id=signed.coordinator_signing_key_id,
            )

        return ClientTrainingTask(
            has_task=True,
            task_id=response.task_id,
            lease_id=response.lease_id,
            client_id=task.client_id,
            round_id=task.round_id,
            model_version=task.model_version,
            algorithm=task.algorithm,
            local_epochs=response.local_epochs,
            batch_size=response.batch_size,
            learning_rate=response.learning_rate,
            fedprox_mu=response.fedprox_mu,
            global_control_variate=_tensor_dict_from_wire(
                response.global_control_variate
            ),
            client_control_variate=_tensor_dict_from_wire(
                response.client_control_variate
            ),
            sample_level_dp_active=response.sample_level_dp_active,
            sample_level_privacy=sample_level_privacy,
            secure_aggregation=SecureAggregationTaskBindingFields(
                secure_aggregation_active=response.secure_aggregation.secure_aggregation_active,
                provider=response.secure_aggregation.provider,
                protocol_version=response.secure_aggregation.protocol_version,
                session_id=response.secure_aggregation.session_id,
                session_configuration_hash=response.secure_aggregation.session_configuration_hash,
                key_advertisement_deadline_unix_s=response.secure_aggregation.key_advertisement_deadline_unix_s,
                minimum_cohort_size=response.secure_aggregation.minimum_cohort_size,
                masked_update_deadline_unix_s=response.secure_aggregation.masked_update_deadline_unix_s,
                session_expiry_unix_s=response.secure_aggregation.session_expiry_unix_s,
                max_absolute_update_bound=response.secure_aggregation.max_absolute_update_bound,
                max_client_weight=response.secure_aggregation.max_client_weight,
                max_aggregate_bound=response.secure_aggregation.max_aggregate_bound,
                tensor_chunk_size=response.secure_aggregation.tensor_chunk_size,
                privacy_mode_compatible=response.secure_aggregation.privacy_mode_compatible,
                privacy_incompatibility_reason=response.secure_aggregation.privacy_incompatibility_reason,
                secure_user_level_dp_active=response.secure_aggregation.secure_user_level_dp_active,
                secure_user_level_adjacency_model=(
                    response.secure_aggregation.secure_user_level_adjacency_model
                ),
                secure_user_level_clip_norm=response.secure_aggregation.secure_user_level_clip_norm,
                secure_user_level_quantization_margin=(
                    response.secure_aggregation.secure_user_level_quantization_margin
                ),
                secure_user_level_effective_sensitivity=(
                    response.secure_aggregation.secure_user_level_effective_sensitivity
                ),
                secure_user_level_noise_multiplier=(
                    response.secure_aggregation.secure_user_level_noise_multiplier
                ),
                secure_user_level_target_delta=response.secure_aggregation.secure_user_level_target_delta,
                secure_user_level_max_epsilon=response.secure_aggregation.secure_user_level_max_epsilon,
                secure_user_level_fixed_weight=response.secure_aggregation.secure_user_level_fixed_weight,
                secure_user_level_sampling_assumption=(
                    response.secure_aggregation.secure_user_level_sampling_assumption
                ),
            ),
            attempt=response.attempt,
            secure_user_level_dp_configuration_hash=secure_user_level_dp_configuration_hash,
        )

    def submit_result(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None = None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None = None,
        personalization_metrics: PersonalizationMetricsSubmission | None = None,
        sample_level_privacy: SampleLevelLedgerEntry | None = None,
        sample_privacy_decision: SampleBudgetDecision | None = None,
    ) -> SubmitOutcome:
        del now
        result = self._worker_pb2.ClientResult(
            run_id=spec.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            base_model_version=task.model_version,
            sample_count=sample_count,
            algorithm=task.algorithm,
            nonce=nonce,
            worker_id=worker_id,
            tensor_manifest=_tensor_manifests_from_dict(delta),
        )
        request = self._pb2.SubmitClientResultRequest(
            worker_id=worker_id,
            task_id=task.task_id,
            lease_id=task.lease_id,
            result=result,
            client_control_variate_delta=_tensor_manifests_from_dict(
                control_delta or {}
            ),
            refreshed_client_control_variate=_tensor_manifests_from_dict(
                refreshed_client_control_variate or {}
            ),
        )
        if sample_level_privacy is not None:
            request.sample_level_privacy.CopyFrom(
                self._privacy_pb2.SampleLevelLedgerEntry(
                    run_id=sample_level_privacy.run_id,
                    round_id=sample_level_privacy.round_id,
                    client_id=sample_level_privacy.client_id,
                    epsilon=sample_level_privacy.epsilon,
                    delta=sample_level_privacy.delta,
                    noise_multiplier=sample_level_privacy.noise_multiplier,
                    sample_rate=sample_level_privacy.sample_rate,
                    steps=sample_level_privacy.steps,
                    accountant=_accountant_type_to_wire(
                        sample_level_privacy.accountant
                    ),
                    recorded_at=sample_level_privacy.recorded_at,
                    entry_id=sample_level_privacy.entry_id,
                )
            )
        if personalization_metrics is not None:
            request.personalization_metrics.CopyFrom(
                self._pb2.PersonalizationMetricRecord(
                    client_id=task.client_id,
                    round_id=task.round_id,
                    algorithm=task.algorithm,
                    global_local_accuracy=personalization_metrics.global_local_accuracy,
                    personalized_local_accuracy=personalization_metrics.personalized_local_accuracy,
                    global_local_loss=personalization_metrics.global_local_loss,
                    personalized_local_loss=personalization_metrics.personalized_local_loss,
                    sample_count=personalization_metrics.sample_count,
                    personalized_model_version=personalization_metrics.personalized_model_version,
                    has_personalized_model=True,
                )
            )
        if self._signing_identity is not None:
            if sample_level_privacy is not None:
                # Privacy Record Authenticity slice, Work Package D
                # (docs/signed-privacy-records.md): "no unsigned privacy
                # record for private production tasks" -- fail closed
                # rather than silently submit an unsigned one when a
                # signing identity is configured but the caller did not
                # supply the budget decision needed to sign it.
                if sample_privacy_decision is None:
                    raise SignedEnvelopeError(
                        "sample_privacy_decision is required to sign a privacy "
                        "record for a private task when a signing identity is set"
                    )
                request.privacy_record_payload.CopyFrom(
                    self._build_signed_sample_privacy_record_payload(
                        worker_id, task, sample_level_privacy, sample_privacy_decision
                    )
                )
                request.privacy_record_envelope.CopyFrom(
                    self._build_signed_sample_privacy_record_envelope(
                        worker_id, request.privacy_record_payload
                    )
                )
            request.envelope.CopyFrom(
                self._build_signed_client_result_envelope(request)
            )
        response = self._grpc_call(self._stub.SubmitClientResult, request)
        self._emit_client_result_outcome_event(
            worker_id=worker_id,
            task=task,
            accepted=response.accepted,
            rejection_code=response.rejection_code,
            included_privacy_record=sample_level_privacy is not None,
        )
        return SubmitOutcome(
            accepted=response.accepted,
            reason=response.reason,
            snapshot=self._run_snapshot(spec.run_id, ""),
        )

    def _emit_client_result_outcome_event(
        self,
        *,
        worker_id: str,
        task: ClientTrainingTask,
        accepted: bool,
        rejection_code: str,
        included_privacy_record: bool,
    ) -> None:
        """Work Package O: SubmitClientResult was, until this slice, the
        one signed-message RPC with no worker-side local event at all --
        the worker learned the coordinator's accept/reject verdict on its
        own signed submission but never recorded it. Mirrors the
        acquire_task pattern above (COORDINATOR_TASK_VERIFIED/REJECTED),
        using the coordinator's own real response rather than re-deriving
        a verdict locally."""
        if accepted:
            self._emit_security_event(
                event_type=_security_event.EVENT_CLIENT_RESULT_ACCEPTED,
                subject_type=_security_event.SUBJECT_TYPE_CLIENT_RESULT,
                worker_id=worker_id,
                task_id=task.task_id,
                outcome=_security_event.OUTCOME_ACCEPTED,
            )
            if included_privacy_record:
                self._emit_security_event(
                    event_type=_security_event.EVENT_PRIVACY_RECORD_ACCEPTED,
                    subject_type=_security_event.SUBJECT_TYPE_PRIVACY_RECORD,
                    worker_id=worker_id,
                    task_id=task.task_id,
                    outcome=_security_event.OUTCOME_ACCEPTED,
                )
            return
        if included_privacy_record and _is_privacy_rejection_code(rejection_code):
            self._emit_security_event(
                event_type=_security_event.EVENT_PRIVACY_RECORD_REJECTED,
                subject_type=_security_event.SUBJECT_TYPE_PRIVACY_RECORD,
                worker_id=worker_id,
                task_id=task.task_id,
                outcome=_security_event.OUTCOME_REJECTED,
                reason_code=rejection_code[:120],
            )
            return
        self._emit_security_event(
            event_type=_security_event.EVENT_CLIENT_RESULT_REJECTED,
            subject_type=_security_event.SUBJECT_TYPE_CLIENT_RESULT,
            worker_id=worker_id,
            task_id=task.task_id,
            outcome=_security_event.OUTCOME_REJECTED,
            reason_code=rejection_code[:120],
        )

    def _build_signed_client_result_envelope(self, request: object) -> object:
        """Builds a signed CLIENT_RESULT envelope for `request` (an
        already-fully-populated SubmitClientResultRequest, minus its own
        `envelope` field). See docs/signed-client-results.md."""
        assert (
            self._signing_identity is not None
        )  # narrows Optional; caller already checked
        assert self._sequence_store is not None
        identity = self._signing_identity
        result = request.result  # type: ignore[attr-defined]

        tensor_manifest = [
            {
                "name": tensor.name,
                "shape": list(tensor.shape),
                "dtype": tensor.dtype,
                "byte_length": tensor.byte_length,
                "checksum": tensor.checksum,
            }
            for tensor in result.tensor_manifest
        ]
        training_metrics = [
            {"name": metric.name, "value": metric.value} for metric in result.metrics
        ]
        personalization_metrics_dict = None
        if request.HasField("personalization_metrics"):  # type: ignore[attr-defined]
            metrics = request.personalization_metrics  # type: ignore[attr-defined]
            personalization_metrics_dict = {
                "algorithm": metrics.algorithm,
                "global_local_accuracy": metrics.global_local_accuracy,
                "global_local_loss": metrics.global_local_loss,
                "has_personalized_model": metrics.has_personalized_model,
                "personalized_improvement": metrics.personalized_improvement,
                "personalized_local_accuracy": metrics.personalized_local_accuracy,
                "personalized_local_loss": metrics.personalized_local_loss,
                "personalized_model_version": metrics.personalized_model_version,
                "sample_count": metrics.sample_count,
            }
        privacy_record_dict = None
        privacy_record_payload_hash = ""
        if request.HasField("sample_level_privacy"):  # type: ignore[attr-defined]
            privacy = request.sample_level_privacy  # type: ignore[attr-defined]
            privacy_record_dict = {
                "accountant": int(privacy.accountant),
                "client_id": privacy.client_id,
                "delta": privacy.delta,
                "entry_id": privacy.entry_id,
                "epsilon": privacy.epsilon,
                "noise_multiplier": privacy.noise_multiplier,
                "round_id": privacy.round_id,
                "run_id": privacy.run_id,
                "sample_rate": privacy.sample_rate,
                "steps": privacy.steps,
            }
            if request.HasField("privacy_record_envelope"):  # type: ignore[attr-defined]
                envelope_field = request.privacy_record_envelope  # type: ignore[attr-defined]
                privacy_record_payload_hash = envelope_field.payload_hash

        hash_input = client_result_payload_hash_input(
            run_id=result.run_id,
            round_id=result.round_id,
            task_id=request.task_id,  # type: ignore[attr-defined]
            client_id=result.client_id,
            worker_id=result.worker_id,
            model_version=result.base_model_version,
            algorithm=result.algorithm,
            sample_count=result.sample_count,
            step_count=result.local_step_count,
            update_norm=result.update_norm,
            completion_timestamp=result.completion_timestamp,
            nonce=result.nonce,
            tensor_manifest=tensor_manifest,
            training_metrics=training_metrics,
            personalization_metrics=personalization_metrics_dict,
            privacy_record=privacy_record_dict,
            privacy_record_payload_hash=privacy_record_payload_hash,
        )
        payload_hash = sha256_hex(hash_input)

        issued_at = security_now()
        sequence_number = self._sequence_store.next_sequence(
            identity.key_id, "client_result"
        )
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_CLIENT_RESULT,
            worker_id=result.worker_id,
            run_id=result.run_id,
            round_id=result.round_id,
            task_id=request.task_id,  # type: ignore[attr-defined]
            client_id=result.client_id,
            model_version=result.base_model_version,
            message_stream=MESSAGE_STREAM_CLIENT_RESULT,
            sequence_number=sequence_number,
            issued_at=issued_at,
            expires_at=issued_at + 3600.0,
            nonce=make_nonce(),
            payload_hash=payload_hash,
            signing_key_id=identity.key_id,
        )
        signed = sign_envelope(fields, identity)
        return self._worker_pb2.SignedWorkerEnvelope(
            schema_version=fields.schema_version,
            message_type=fields.message_type,
            worker_id=fields.worker_id,
            run_id=fields.run_id,
            round_id=fields.round_id,
            task_id=fields.task_id,
            client_id=fields.client_id,
            model_version=fields.model_version,
            message_stream=fields.message_stream,
            sequence_number=fields.sequence_number,
            issued_at=fields.issued_at,
            expires_at=fields.expires_at,
            nonce=fields.nonce,
            payload_hash=fields.payload_hash,
            signing_key_id=fields.signing_key_id,
            signature=signed.signature_hex,
        )

    def _build_signed_sample_privacy_record_payload(
        self,
        worker_id: str,
        task: ClientTrainingTask,
        sample_level_privacy: SampleLevelLedgerEntry,
        decision: SampleBudgetDecision,
        *,
        is_hybrid: bool = False,
    ) -> object:
        """Builds the domain-payload fields of a
        fl.privacy.v1.SignedSamplePrivacyRecord (docs/signed-privacy-records.md).
        Binds to `sample_level_privacy` (the real, already-computed
        ledger entry) and `decision` (the real SampleBudgetEnforcer
        decision that governed this task), never independently
        recomputed or approximated.

        privacy_mode is PRIVACY_MODE_SAMPLE_LEVEL_DP (2) unless
        `is_hybrid=True`, in which case it is PRIVACY_MODE_HYBRID_DP (4)
        -- Secure Hybrid Differential Privacy Runtime slice: closes the
        pre-existing gap this function's docstring used to describe
        ("a worker cannot currently distinguish hybrid DP... from pure
        sample-level DP from its own vantage point"). The cleartext
        submission path (submit_result, below) never passes
        is_hybrid=True today (the cleartext hybrid mechanism's own
        coordinator-side clip/noise path does not consult this
        worker-signed record's privacy_mode field the way the secure
        path's new verification does) -- only submit_masked_update's
        real hybrid path does, where the worker genuinely knows both
        mechanisms are active (task.sample_level_dp_active AND
        handshake.secure_user_level_dp_active).
        secure_random_required is hardcoded to False: this codebase's
        SampleLevelDPConfig wire message carries no such field (unlike
        UserLevelDPConfig.secure_random) -- see proto/privacy/privacy.proto.
        """
        config = task.sample_level_privacy
        assert (
            config is not None
        )  # caller only reaches here when sample_level_dp_active
        secure_random_available = opacus_secure_mode_available()
        configuration_hash = sample_privacy_configuration_hash(
            noise_multiplier=config.noise_multiplier,
            max_grad_norm=config.max_grad_norm,
            target_delta=config.target_delta,
            epsilon_budget=config.epsilon_budget,
            sample_budget_policy=_sample_budget_policy_to_wire(
                config.sample_budget_policy
            ),
            poisson_sampling=config.poisson_sampling,
        )
        return self._privacy_pb2.SignedSamplePrivacyRecord(
            schema_version=1,
            worker_id=worker_id,
            run_id=sample_level_privacy.run_id,
            round_id=sample_level_privacy.round_id,
            task_id=task.task_id,
            client_id=sample_level_privacy.client_id,
            model_version=task.model_version,
            algorithm=task.algorithm,
            privacy_mode=4 if is_hybrid else 2,  # HYBRID_DP : SAMPLE_LEVEL_DP
            accountant_type=_accountant_type_to_wire(sample_level_privacy.accountant),
            accountant_step=sample_level_privacy.steps,
            epsilon=sample_level_privacy.epsilon,
            delta=sample_level_privacy.delta,
            noise_multiplier=sample_level_privacy.noise_multiplier,
            max_grad_norm=config.max_grad_norm,
            sample_rate=sample_level_privacy.sample_rate,
            expected_batch_size=task.batch_size,
            local_epochs=task.local_epochs,
            configuration_hash=configuration_hash,
            accountant_state_hash=decision.accountant_state_hash,
            budget_target_epsilon=decision.budget,
            budget_target_delta=config.target_delta,
            budget_policy=_sample_budget_policy_to_wire(decision.policy),
            budget_decision=decision.outcome.value,
            secure_random_required=False,
            secure_random_available=secure_random_available,
            secure_random_provider="opacus_secure_mode(torchcsprng)"
            if secure_random_available
            else "none",
        )

    def _build_signed_sample_privacy_record_envelope(
        self, worker_id: str, payload: object
    ) -> object:
        """Wraps `payload` (an already-built SignedSamplePrivacyRecord)
        in a SignedWorkerEnvelope -- message_type =
        MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD, message_stream =
        MESSAGE_STREAM_PRIVACY_RECORD, payload_hash = SHA-256 of the
        payload's canonical JSON. See docs/signed-privacy-records.md's
        "Two independent signatures" design."""
        assert self._signing_identity is not None
        assert self._sequence_store is not None
        identity = self._signing_identity

        fields = SamplePrivacyRecordFields(
            worker_id=payload.worker_id,  # type: ignore[attr-defined]
            run_id=payload.run_id,  # type: ignore[attr-defined]
            round_id=payload.round_id,  # type: ignore[attr-defined]
            task_id=payload.task_id,  # type: ignore[attr-defined]
            client_id=payload.client_id,  # type: ignore[attr-defined]
            model_version=payload.model_version,  # type: ignore[attr-defined]
            algorithm=payload.algorithm,  # type: ignore[attr-defined]
            privacy_mode=int(payload.privacy_mode),  # type: ignore[attr-defined]
            accountant_type=int(payload.accountant_type),  # type: ignore[attr-defined]
            accountant_step=payload.accountant_step,  # type: ignore[attr-defined]
            epsilon=payload.epsilon,  # type: ignore[attr-defined]
            delta=payload.delta,  # type: ignore[attr-defined]
            noise_multiplier=payload.noise_multiplier,  # type: ignore[attr-defined]
            max_grad_norm=payload.max_grad_norm,  # type: ignore[attr-defined]
            sample_rate=payload.sample_rate,  # type: ignore[attr-defined]
            expected_batch_size=payload.expected_batch_size,  # type: ignore[attr-defined]
            local_epochs=payload.local_epochs,  # type: ignore[attr-defined]
            configuration_hash=payload.configuration_hash,  # type: ignore[attr-defined]
            accountant_state_hash=payload.accountant_state_hash,  # type: ignore[attr-defined]
            budget_target_epsilon=payload.budget_target_epsilon,  # type: ignore[attr-defined]
            budget_target_delta=payload.budget_target_delta,  # type: ignore[attr-defined]
            budget_policy=int(payload.budget_policy),  # type: ignore[attr-defined]
            budget_decision=payload.budget_decision,  # type: ignore[attr-defined]
            secure_random_required=payload.secure_random_required,  # type: ignore[attr-defined]
            secure_random_available=payload.secure_random_available,  # type: ignore[attr-defined]
            secure_random_provider=payload.secure_random_provider,  # type: ignore[attr-defined]
        )
        hash_input = sample_privacy_record_payload_hash_input(fields)
        payload_hash = sha256_hex(hash_input)

        issued_at = security_now()
        sequence_number = self._sequence_store.next_sequence(
            identity.key_id, "privacy_record"
        )
        envelope_fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD,
            worker_id=worker_id,
            run_id=fields.run_id,
            round_id=fields.round_id,
            task_id=fields.task_id,
            client_id=fields.client_id,
            model_version=fields.model_version,
            message_stream=MESSAGE_STREAM_PRIVACY_RECORD,
            sequence_number=sequence_number,
            issued_at=issued_at,
            expires_at=issued_at + 3600.0,
            nonce=make_nonce(),
            payload_hash=payload_hash,
            signing_key_id=identity.key_id,
        )
        signed = sign_envelope(envelope_fields, identity)
        return self._worker_pb2.SignedWorkerEnvelope(
            schema_version=envelope_fields.schema_version,
            message_type=envelope_fields.message_type,
            worker_id=envelope_fields.worker_id,
            run_id=envelope_fields.run_id,
            round_id=envelope_fields.round_id,
            task_id=envelope_fields.task_id,
            client_id=envelope_fields.client_id,
            model_version=envelope_fields.model_version,
            message_stream=envelope_fields.message_stream,
            sequence_number=envelope_fields.sequence_number,
            issued_at=envelope_fields.issued_at,
            expires_at=envelope_fields.expires_at,
            nonce=envelope_fields.nonce,
            payload_hash=envelope_fields.payload_hash,
            signing_key_id=envelope_fields.signing_key_id,
            signature=signed.signature_hex,
        )

    def rotate_signing_key(
        self,
        worker_id: str,
        new_identity: WorkerSigningIdentity,
        *,
        new_key_expires_at_unix_s: float = 0.0,
        requested_grace_period_seconds: float = 3600.0,
    ) -> object:
        """Signing-Key Lifecycle slice (docs/key-rotation.md): submits a
        real signed key-rotation request, signed by the CURRENT key
        (self._signing_identity), requesting that `new_identity`'s
        public key become this worker's new preferred signing key.

        Does NOT mark `new_identity` as this client's signing identity
        on its own -- the caller must persist `new_identity`'s private
        key *before* calling this (never replace the current key's own
        file first -- see docs/key-rotation.md's "do not replace the
        current private key before coordinator acceptance" requirement)
        and should only start using `new_identity` for subsequent
        register_worker()/submit_result() calls once this method
        returns response.accepted == True. Raises SignedEnvelopeError
        if no signing_identity is currently configured (there is no
        "current key" to sign the rotation request with).
        """
        if self._signing_identity is None or self._sequence_store is None:
            raise SignedEnvelopeError(
                "cannot rotate a signing key without a current signing_identity "
                "configured"
            )
        current = self._signing_identity

        fields = WorkerKeyRotationFields(
            worker_id=worker_id,
            current_signing_key_id=current.key_id,
            new_signing_key_id=new_identity.key_id,
            new_public_key_hex=new_identity.public_key_hex(),
            new_key_expires_at_unix_s=new_key_expires_at_unix_s,
            requested_grace_period_seconds=requested_grace_period_seconds,
        )
        hash_input = rotation_payload_hash_input(fields)
        payload_hash = sha256_hex(hash_input)

        issued_at = security_now()
        sequence_number = self._sequence_store.next_sequence(
            current.key_id, "key_rotation"
        )
        envelope_fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_KEY_ROTATION_REQUEST,
            worker_id=worker_id,
            message_stream=MESSAGE_STREAM_KEY_MANAGEMENT,
            sequence_number=sequence_number,
            issued_at=issued_at,
            expires_at=issued_at + 3600.0,
            nonce=make_nonce(),
            payload_hash=payload_hash,
            signing_key_id=current.key_id,
        )
        # Signed with the CURRENT key -- this is the entire point of a
        # rotation request: only an already-trusted key may authorize
        # its own successor, never the new (as-yet-unregistered) key.
        signed = sign_envelope(envelope_fields, current)
        envelope = self._worker_pb2.SignedWorkerEnvelope(
            schema_version=envelope_fields.schema_version,
            message_type=envelope_fields.message_type,
            worker_id=envelope_fields.worker_id,
            message_stream=envelope_fields.message_stream,
            sequence_number=envelope_fields.sequence_number,
            issued_at=envelope_fields.issued_at,
            expires_at=envelope_fields.expires_at,
            nonce=envelope_fields.nonce,
            payload_hash=envelope_fields.payload_hash,
            signing_key_id=envelope_fields.signing_key_id,
            signature=signed.signature_hex,
        )
        payload = self._worker_pb2.WorkerKeyRotationPayload(
            schema_version=fields.schema_version,
            worker_id=fields.worker_id,
            current_signing_key_id=fields.current_signing_key_id,
            new_signing_key_id=fields.new_signing_key_id,
            new_public_key_hex=fields.new_public_key_hex,
            new_key_expires_at_unix_s=fields.new_key_expires_at_unix_s,
            requested_grace_period_seconds=fields.requested_grace_period_seconds,
        )
        request = self._pb2.RotateWorkerSigningKeyRequest(
            worker_id=worker_id, payload=payload, envelope=envelope
        )
        response = self._grpc_call(self._stub.RotateWorkerSigningKey, request)
        if response.accepted:
            # Only now -- after real coordinator acceptance -- does this
            # client start signing subsequent messages with the new key.
            self._signing_identity = new_identity
        return response

    def trusted_coordinator_keys(self) -> dict[str, TrustedCoordinatorKey] | None:
        """Secure Cohort Handshake and Signed Roster Runtime slice
        (docs/secure-cohort-handshake-foundation.md), Work item 12: the
        same trust bundle acquire_task's signed_task verification
        already uses, exposed for frozen-roster verification (which
        WorkerService performs itself -- see
        fl_platform.secure_aggregation.key_advertisement.verify_frozen_cohort_roster).
        Returns None when this client has no trusted_coordinator_keys_path
        configured, same convention as every other Optional store on
        this class."""
        if self._trust_bundle_reloader is None:
            return None
        self.reload_trusted_coordinator_keys()
        return self._trust_bundle_reloader.current_keys()

    def advertise_secure_aggregation_key(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        ephemeral_public_key: bytes,
    ) -> SecureAggregationKeyAdvertisementOutcome:
        """Secure Cohort Handshake and Signed Roster Runtime slice
        (docs/secure-cohort-handshake-foundation.md), Work items 6/7/8:
        builds and sends this worker's signed ephemeral X25519 key
        advertisement for `task`'s secure aggregation session
        (task.secure_aggregation -- caller must have already checked
        secure_aggregation_active is True). Requires a signing_identity,
        same as rotate_signing_key/submit_security_events. Signed with a
        real wall-clock issued_at (via build_signed_key_advertisement's
        own time.time() default), not WorkerService.run()'s synthetic
        loop `now` -- the coordinator enforces real freshness/expiry on
        this envelope, unlike the journal timestamps `now` otherwise
        feeds."""
        if self._signing_identity is None or self._sequence_store is None:
            raise SignedEnvelopeError(
                "cannot advertise a secure aggregation key without a signing_identity "
                "configured"
            )
        binding = task.secure_aggregation
        sequence_number = self._sequence_store.next_sequence(
            self._signing_identity.key_id, "secure_aggregation_key_advertisement"
        )
        advertisement_fields, signed_envelope = build_signed_key_advertisement(
            session_id=binding.session_id,
            run_id=spec.run_id,
            round_id=task.round_id,
            model_version=task.model_version,
            worker_id=worker_id,
            client_id=task.client_id,
            ephemeral_public_key=ephemeral_public_key,
            signing_identity=self._signing_identity,
            sequence_number=sequence_number,
            nonce=make_nonce(),
        )
        envelope_fields = signed_envelope.fields
        envelope = self._worker_pb2.SignedWorkerEnvelope(
            schema_version=envelope_fields.schema_version,
            message_type=envelope_fields.message_type,
            worker_id=envelope_fields.worker_id,
            run_id=envelope_fields.run_id,
            round_id=envelope_fields.round_id,
            client_id=envelope_fields.client_id,
            model_version=envelope_fields.model_version,
            message_stream=envelope_fields.message_stream,
            sequence_number=envelope_fields.sequence_number,
            issued_at=envelope_fields.issued_at,
            expires_at=envelope_fields.expires_at,
            nonce=envelope_fields.nonce,
            payload_hash=envelope_fields.payload_hash,
            signing_key_id=envelope_fields.signing_key_id,
            signature=signed_envelope.signature_hex,
        )
        key_advertisement = self._worker_pb2.SecureAggregationKeyAdvertisement(
            schema_version=advertisement_fields.schema_version,
            session_id=advertisement_fields.session_id,
            run_id=advertisement_fields.run_id,
            round_id=advertisement_fields.round_id,
            model_version=advertisement_fields.model_version,
            worker_id=advertisement_fields.worker_id,
            client_id=advertisement_fields.client_id,
            ephemeral_public_key_x25519=advertisement_fields.ephemeral_public_key_x25519,
            public_key_fingerprint=advertisement_fields.public_key_fingerprint,
            issued_at=advertisement_fields.issued_at,
            expires_at=advertisement_fields.expires_at,
        )
        request = self._pb2.AdvertiseSecureAggregationKeyRequest(
            envelope=envelope, key_advertisement=key_advertisement
        )
        response = self._grpc_call(self._stub.AdvertiseSecureAggregationKey, request)
        return SecureAggregationKeyAdvertisementOutcome(
            accepted=response.accepted,
            reason=response.reason,
            rejection_reason=self._pb2.SecureAggregationRejectionReason.Name(
                response.rejection_reason
            ),
        )

    def get_frozen_cohort_roster(
        self, worker_id: str, session_id: str
    ) -> FrozenCohortRosterOutcome:
        """Secure Cohort Handshake and Signed Roster Runtime slice, Work
        item 12: fetches the coordinator-signed frozen roster for
        `session_id`. `available=False` (with `roster=None`) before
        cohort freeze or for an unknown session -- never an error
        response (see GetFrozenCohortRosterResponse's own comment: the
        roster must never be exposed before freeze)."""
        request = self._pb2.GetFrozenCohortRosterRequest(
            worker_id=worker_id, session_id=session_id
        )
        response = self._grpc_call(self._stub.GetFrozenCohortRoster, request)
        if not response.available:
            return FrozenCohortRosterOutcome(
                available=False, reason=response.reason, roster=None
            )
        return FrozenCohortRosterOutcome(
            available=True, reason=response.reason, roster=response.roster
        )

    def submit_masked_update(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        masked_tensors: dict[str, list[int]],
        masked_weight: int,
        encoding: object,
        roster: object,
        secure_user_level_dp_active: bool = False,
        clip_norm: float = 0.0,
        effective_sensitivity: float = 0.0,
        sample_level_privacy: SampleLevelLedgerEntry | None = None,
        sample_privacy_decision: SampleBudgetDecision | None = None,
    ) -> MaskedUpdateSubmissionOutcome:
        """Masked Update Runtime and No-Dropout Secure FedAvg
        Finalization slice, Work Areas K/L/M/N: builds and sends this
        worker's signed MaskedClientUpdate for `task`'s frozen secure
        aggregation cohort. `masked_tensors`/`masked_weight` are
        already-masked ring values (WorkerService._encode_and_mask_local_update's
        output) -- this method only builds, signs, and submits, it
        never encodes or masks anything itself. `encoding` is a real
        WeightedEncodingResult and `roster` a real RosterContext
        (typed as `object` here only to avoid a hard import-time
        dependency on fl_platform.secure_aggregation.masked_update from
        every caller of this module -- the real objects are passed
        through unchanged). Requires a signing_identity, same as
        advertise_secure_aggregation_key.

        Secure User-Level Differential Privacy Runtime slice, Work Area
        K/I: when `secure_user_level_dp_active`, builds and signs a
        real SignedUserLevelPrivacyAttestation with the SAME
        signing_identity as the outer envelope (Work Area J's "same
        key" binding) and attaches it. Deliberately takes only
        `clip_norm`/`effective_sensitivity` as inputs -- never an
        unclipped norm or a clipping factor (this method cannot leak
        what it is never given). `operation_completed=True` is always
        correct here: clipping, encoding, and masking have already
        happened by the time this method is called (a masking failure
        raises before reaching this call site, per
        WorkerService.run()'s own branching).

        Secure Hybrid Differential Privacy Runtime slice, Work Areas
        K/L: when `sample_level_privacy` is not None (this task trained
        under sample-level DP -- SAMPLE_LEVEL_DP alone, or HYBRID_DP,
        under secure aggregation), builds and signs a real
        SignedSamplePrivacyRecord exactly as submit_result already does
        for the cleartext path, attaches both the envelope and payload
        to the request, and sets sample_privacy_record_hash to that
        envelope's own payload_hash -- closing the pre-existing gap
        where this field was always transmitted empty (see
        docs/secure-hybrid-dp-runtime-audit.md). is_hybrid is derived
        from secure_user_level_dp_active (the worker's own genuine
        signal that both mechanisms are active for this task)."""
        if self._signing_identity is None or self._sequence_store is None:
            raise SignedEnvelopeError(
                "cannot submit a masked update without a signing_identity configured"
            )
        sample_privacy_record_hash = ""
        sample_privacy_envelope = None
        sample_privacy_payload = None
        if sample_level_privacy is not None:
            if sample_privacy_decision is None:
                raise SignedEnvelopeError(
                    "sample_privacy_decision is required to sign a sample privacy "
                    "record for a secure-aggregation-bound task with sample-level "
                    "DP active"
                )
            sample_privacy_payload = self._build_signed_sample_privacy_record_payload(
                worker_id, task, sample_level_privacy, sample_privacy_decision,
                is_hybrid=secure_user_level_dp_active,
            )
            sample_privacy_envelope = self._build_signed_sample_privacy_record_envelope(
                worker_id, sample_privacy_payload
            )
            sample_privacy_record_hash = sample_privacy_envelope.payload_hash  # type: ignore[attr-defined]
        sequence_number = self._sequence_store.next_sequence(
            self._signing_identity.key_id, "secure_aggregation_masked_update"
        )
        update_fields, signed_envelope = build_signed_masked_update(
            masked_tensors=masked_tensors,
            masked_weight=masked_weight,
            encoding=encoding,  # type: ignore[arg-type]
            roster=roster,  # type: ignore[arg-type]
            task_id=task.task_id,
            lease_id=task.lease_id,
            attempt=task.attempt,
            worker_id=worker_id,
            client_id=task.client_id,
            sample_privacy_record_hash=sample_privacy_record_hash,
            signing_identity=self._signing_identity,
            sequence_number=sequence_number,
            nonce=make_nonce(),
        )
        envelope_fields = signed_envelope.fields
        envelope = self._worker_pb2.SignedWorkerEnvelope(
            schema_version=envelope_fields.schema_version,
            message_type=envelope_fields.message_type,
            worker_id=envelope_fields.worker_id,
            run_id=envelope_fields.run_id,
            round_id=envelope_fields.round_id,
            task_id=envelope_fields.task_id,
            client_id=envelope_fields.client_id,
            model_version=envelope_fields.model_version,
            message_stream=envelope_fields.message_stream,
            sequence_number=envelope_fields.sequence_number,
            issued_at=envelope_fields.issued_at,
            expires_at=envelope_fields.expires_at,
            nonce=envelope_fields.nonce,
            payload_hash=envelope_fields.payload_hash,
            signing_key_id=envelope_fields.signing_key_id,
            signature=signed_envelope.signature_hex,
        )
        masked_update = self._worker_pb2.MaskedClientUpdate(
            schema_version=update_fields.schema_version,
            provider=update_fields.provider,
            protocol_version=update_fields.protocol_version,
            session_id=update_fields.session_id,
            run_id=update_fields.run_id,
            round_id=update_fields.round_id,
            task_id=update_fields.task_id,
            lease_id=update_fields.lease_id,
            attempt=update_fields.attempt,
            worker_id=update_fields.worker_id,
            client_id=update_fields.client_id,
            model_version=update_fields.model_version,
            cohort_commitment=update_fields.cohort_commitment,
            tensor_manifest_hash=update_fields.tensor_manifest_hash,
            fixed_point_profile_hash=update_fields.fixed_point_profile_hash,
            frozen_roster_payload_hash=update_fields.frozen_roster_payload_hash,
            cryptographic_profile_hash=update_fields.cryptographic_profile_hash,
            masked_tensors=[
                self._worker_pb2.SecureAggregationMaskedTensor(
                    tensor_name=tensor.tensor_name,
                    masked_values=list(tensor.masked_values),
                    checksum=tensor.checksum,
                )
                for tensor in update_fields.masked_tensors
            ],
            masked_weight=update_fields.masked_weight,
            masked_weight_checksum=update_fields.masked_weight_checksum,
            encoding_statistics=self._worker_pb2.SecureAggregationEncodingStatistics(
                total_elements=update_fields.encoding_statistics.total_elements,
                max_quantization_error=update_fields.encoding_statistics.max_quantization_error,
                mean_quantization_error=update_fields.encoding_statistics.mean_quantization_error,
            ),
            sample_privacy_record_hash=update_fields.sample_privacy_record_hash,
            issued_at=update_fields.issued_at,
            expires_at=update_fields.expires_at,
        )
        if secure_user_level_dp_active:
            attestation_fields = build_signed_user_level_privacy_attestation(
                worker_id=worker_id,
                client_id=task.client_id,
                run_id=roster.run_id,  # type: ignore[attr-defined]
                round_id=roster.round_id,  # type: ignore[attr-defined]
                task_id=task.task_id,
                session_id=roster.session_id,  # type: ignore[attr-defined]
                model_version=roster.model_version,  # type: ignore[attr-defined]
                # Secure Hybrid Differential Privacy Runtime slice: HYBRID_DP
                # when a sample-level record is also being submitted
                # alongside this attestation, matching the same
                # is_hybrid signal used for the sample record's own
                # privacy_mode above -- previously always
                # PRIVACY_MODE_USER_LEVEL_DP even for a hybrid round.
                privacy_mode=self._privacy_pb2.PRIVACY_MODE_HYBRID_DP
                if sample_level_privacy is not None
                else self._privacy_pb2.PRIVACY_MODE_USER_LEVEL_DP,
                privacy_configuration_hash=task.secure_user_level_dp_configuration_hash,
                clip_norm=clip_norm,
                effective_sensitivity=effective_sensitivity,
                fixed_point_profile_hash=roster.fixed_point_profile_hash,  # type: ignore[attr-defined]
                tensor_manifest_hash=roster.tensor_manifest_hash,  # type: ignore[attr-defined]
                provider=roster.provider,  # type: ignore[attr-defined]
                operation_completed=True,
                signing_identity=self._signing_identity,
            )
            masked_update.user_level_attestation.CopyFrom(
                self._worker_pb2.SignedUserLevelPrivacyAttestation(
                    schema_version=attestation_fields.schema_version,
                    worker_id=attestation_fields.worker_id,
                    client_id=attestation_fields.client_id,
                    run_id=attestation_fields.run_id,
                    round_id=attestation_fields.round_id,
                    task_id=attestation_fields.task_id,
                    session_id=attestation_fields.session_id,
                    model_version=attestation_fields.model_version,
                    privacy_mode=attestation_fields.privacy_mode,
                    privacy_configuration_hash=attestation_fields.privacy_configuration_hash,
                    clip_norm=attestation_fields.clip_norm,
                    effective_sensitivity=attestation_fields.effective_sensitivity,
                    clipping_strategy=attestation_fields.clipping_strategy,
                    fixed_weight=attestation_fields.fixed_weight,
                    fixed_point_profile_hash=attestation_fields.fixed_point_profile_hash,
                    tensor_manifest_hash=attestation_fields.tensor_manifest_hash,
                    provider=attestation_fields.provider,
                    operation_completed=attestation_fields.operation_completed,
                    issued_at=attestation_fields.issued_at,
                    expires_at=attestation_fields.expires_at,
                    signing_key_id=attestation_fields.signing_key_id,
                    payload_hash=attestation_fields.payload_hash,
                    signature=attestation_fields.signature,
                )
            )
        request = self._pb2.SubmitMaskedClientUpdateRequest(
            envelope=envelope, masked_update=masked_update
        )
        if sample_privacy_envelope is not None and sample_privacy_payload is not None:
            request.sample_privacy_record_envelope.CopyFrom(sample_privacy_envelope)
            request.sample_privacy_record_payload.CopyFrom(sample_privacy_payload)
        response = self._grpc_call(self._stub.SubmitMaskedClientUpdate, request)
        return MaskedUpdateSubmissionOutcome(
            accepted=response.accepted,
            reason=response.reason,
            rejection_reason=self._pb2.SecureAggregationRejectionReason.Name(
                response.rejection_reason
            ),
        )

    def submit_security_events(
        self, worker_id: str, *, max_batch_size: int = 100
    ) -> object | None:
        """Web Security Center, Event Centralization, and Security CI
        slice (docs/security-event-centralization.md): flushes the
        worker's locally-queued security events (see
        self._security_event_queue) to the coordinator as one signed
        batch, reusing the exact SignedWorkerEnvelope pipeline
        rotate_signing_key uses above -- signed by the CURRENT key,
        verified by the coordinator against WorkerIdentityRegistry/
        SigningKeyRegistry, replay-protected on its own
        MESSAGE_STREAM_SECURITY_EVENTS sequence track.

        No-op (returns None) if security_event_journal_path/
        signing_identity were never configured, or if there is nothing
        pending -- callers can call this unconditionally on a periodic
        timer without checking configuration first, matching
        _emit_security_event's existing "no-op unless configured"
        convention.

        Only advances the local queue's acknowledgment cursor when
        response.accepted is True (a batch-level accept, independent of
        whether the coordinator skipped any individual malformed event
        within it -- see SubmitWorkerSecurityEvents's proto comment).
        Whether an individual event was itself accepted or skipped is
        coordinator-side observability info the worker does not need in
        order to decide whether to retry: retrying an already-accepted
        batch would only be safe because of the replay/idempotency
        pipeline, but skipping the retry entirely (by advancing the
        cursor on any accepted response) is simpler and avoids relying
        on that as a safety net for routine operation.
        """
        if (
            self._security_event_queue is None
            or self._signing_identity is None
            or self._sequence_store is None
        ):
            return None
        pending = self._security_event_queue.select_pending(max_batch_size)
        if not pending:
            return None
        current = self._signing_identity

        event_fields = tuple(
            WorkerSecurityEventFields(
                event_type=event.event_type,
                severity=event.severity,
                timestamp=event.timestamp,
                actor_type=event.actor_type,
                safe_actor_id=event.safe_actor_id,
                subject_type=event.subject_type,
                safe_subject_id=event.safe_subject_id,
                outcome=event.outcome,
                source_component=event.source_component,
                run_id=event.run_id,
                round_id=event.round_id,
                task_id=event.task_id,
                safe_signing_key_id=event.safe_signing_key_id,
                request_id=event.request_id,
                trace_id=event.trace_id,
                reason_code=event.reason_code,
                safe_details=dict(event.safe_details),
                schema_version=event.schema_version,
            )
            for event in pending
        )
        batch_fields = WorkerSecurityEventBatchFields(
            worker_id=worker_id,
            events=event_fields,
            queue_depth_hint=self._security_event_queue.pending_count_hint(),
        )
        hash_input = security_event_batch_payload_hash_input(batch_fields)
        payload_hash = sha256_hex(hash_input)

        issued_at = security_now()
        sequence_number = self._sequence_store.next_sequence(
            current.key_id, "security_events"
        )
        envelope_fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_SECURITY_EVENT_BATCH,
            worker_id=worker_id,
            message_stream=MESSAGE_STREAM_SECURITY_EVENTS,
            sequence_number=sequence_number,
            issued_at=issued_at,
            expires_at=issued_at + 3600.0,
            nonce=make_nonce(),
            payload_hash=payload_hash,
            signing_key_id=current.key_id,
        )
        signed = sign_envelope(envelope_fields, current)
        envelope = self._worker_pb2.SignedWorkerEnvelope(
            schema_version=envelope_fields.schema_version,
            message_type=envelope_fields.message_type,
            worker_id=envelope_fields.worker_id,
            message_stream=envelope_fields.message_stream,
            sequence_number=envelope_fields.sequence_number,
            issued_at=envelope_fields.issued_at,
            expires_at=envelope_fields.expires_at,
            nonce=envelope_fields.nonce,
            payload_hash=envelope_fields.payload_hash,
            signing_key_id=envelope_fields.signing_key_id,
            signature=signed.signature_hex,
        )
        batch = self._worker_pb2.SignedWorkerSecurityEventBatch(
            schema_version=batch_fields.schema_version,
            worker_id=batch_fields.worker_id,
            events=[
                self._worker_pb2.WorkerSecurityEventPayload(
                    schema_version=fields.schema_version,
                    event_type=fields.event_type,
                    severity=fields.severity,
                    timestamp=fields.timestamp,
                    source_component=fields.source_component,
                    actor_type=fields.actor_type,
                    safe_actor_id=fields.safe_actor_id,
                    subject_type=fields.subject_type,
                    safe_subject_id=fields.safe_subject_id,
                    run_id=fields.run_id,
                    round_id=fields.round_id,
                    task_id=fields.task_id,
                    safe_signing_key_id=fields.safe_signing_key_id,
                    request_id=fields.request_id,
                    trace_id=fields.trace_id,
                    outcome=fields.outcome,
                    reason_code=fields.reason_code,
                    safe_details=dict(fields.safe_details or {}),
                )
                for fields in event_fields
            ],
            queue_depth_hint=batch_fields.queue_depth_hint,
        )
        request = self._pb2.SubmitWorkerSecurityEventsRequest(
            worker_id=worker_id, batch=batch, envelope=envelope
        )
        response = self._grpc_call(self._stub.SubmitWorkerSecurityEvents, request)
        if response.accepted:
            self._security_event_queue.mark_acknowledged(pending[-1].event_id)
        return response  # type: ignore[no-any-return]

    def get_personalization_summary(
        self, spec: RunSpec, now: float
    ) -> list[PersonalizationMetricRecord]:
        del now
        response = self._grpc_call(
            self._stub.GetPersonalizationSummary,
            self._pb2.GetPersonalizationSummaryRequest(run_id=spec.run_id),
        )
        return [
            PersonalizationMetricRecord(
                client_id=record.client_id,
                round_id=record.round_id,
                algorithm=record.algorithm,
                global_local_accuracy=record.global_local_accuracy,
                personalized_local_accuracy=record.personalized_local_accuracy,
                personalized_improvement=record.personalized_improvement,
                sample_count=record.sample_count,
            )
            for record in response.records
        ]


def _tensor_manifests_from_dict(tensors: dict[str, torch.Tensor]) -> list[object]:
    """Encodes real tensor values inline on TensorManifest.values — see
    docs/create-run-wire-mapping.md's "tensor transport" section for why
    an artifact-store upload/download path is not used here.

    Signed Client Results and Worker Lifecycle Enforcement slice
    (docs/payload-hashing.md): also computes and sets dtype/byte_length/
    checksum — previously always left empty, meaning nothing ever bound
    a signature (or anything else) to a tensor's actual values. dtype is
    "float64", not the source tensor's own dtype (typically float32):
    ``.tolist()`` on a flattened tensor always yields Python floats
    (64-bit), which is what actually crosses the wire as
    ``repeated double values`` — dtype/byte_length/checksum describe
    the wire representation, not the in-memory PyTorch tensor, so they
    must agree with what a receiver can actually reconstruct and
    re-hash from ``values`` alone.
    """
    import hashlib  # noqa: PLC0415
    import struct  # noqa: PLC0415

    from worker import worker_pb2  # noqa: PLC0415

    manifests = []
    for name, tensor in tensors.items():
        flat = tensor.detach().flatten().tolist()
        packed = struct.pack(
            f"<{len(flat)}d", *flat
        )  # little-endian float64, canonical order
        checksum = hashlib.sha256(packed).hexdigest()
        manifests.append(
            worker_pb2.TensorManifest(
                name=name,
                shape=list(tensor.shape),
                dtype="float64",
                byte_length=len(packed),
                checksum=checksum,
                values=flat,
            )
        )
    return manifests


def _tensor_dict_from_wire(manifests: Iterable[Any]) -> dict[str, torch.Tensor]:
    result: dict[str, torch.Tensor] = {}
    for manifest in manifests:
        shape = tuple(manifest.shape)
        result[manifest.name] = torch.tensor(
            list(manifest.values), dtype=torch.float32
        ).reshape(shape)
    return result


# fl.privacy.v1.AccountantType <-> the plain strings SUPPORTED_ACCOUNTANTS
# uses everywhere else in this codebase (matches only what the installed
# Opacus version actually supports — see privacy/accounting.py).
_ACCOUNTANT_TYPE_TO_WIRE = {"rdp": 1, "prv": 2, "gdp": 3}
_ACCOUNTANT_TYPE_FROM_WIRE = {
    value: key for key, value in _ACCOUNTANT_TYPE_TO_WIRE.items()
}


def _accountant_type_to_wire(accountant: str) -> int:
    return _ACCOUNTANT_TYPE_TO_WIRE.get(accountant, 0)


def _accountant_type_from_wire(value: int) -> str:
    return _ACCOUNTANT_TYPE_FROM_WIRE.get(value, "rdp")


# fl.privacy.v1.SampleBudgetPolicy <-> SamplePrivacyBudgetPolicy's string
# values (privacy/config.py) -- Privacy Record Authenticity slice.
_SAMPLE_BUDGET_POLICY_TO_WIRE = {
    "warn_only": 1,
    "stop_before_exceeding": 2,
    "stop_after_current_task": 3,
    "fail_task": 4,
}


def _sample_budget_policy_to_wire(policy: object) -> int:
    return _SAMPLE_BUDGET_POLICY_TO_WIRE.get(
        str(policy.value if hasattr(policy, "value") else policy), 0
    )
