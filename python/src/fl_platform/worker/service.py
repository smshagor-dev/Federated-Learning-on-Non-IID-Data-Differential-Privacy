"""Worker execution loop: register -> heartbeat -> acquire task -> train ->
submit -> repeat, with graceful handling of the failure modes listed in
the Coordinator Runtime phase task (coordinator unavailable, registration failure,
cancellation, invalid manifest, missing dataset, training exceptions,
submission retry, shutdown signal).
"""

from __future__ import annotations

import logging
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import torch

from fl_platform.privacy import (
    SampleLevelLedgerEntry,
    record_sample_level_training_rejected,
    record_sample_level_training_success,
)
from fl_platform.privacy.budget_enforcement import (
    SampleBudgetDecision,
    SampleBudgetEnforcer,
    SampleLevelBudgetExceededError,
)
from fl_platform.privacy.secure_random import SecureRandomTaskRejectedError
from fl_platform.secure_aggregation.fixed_point_encoding import (
    FixedPointEncodingProfile,
)
from fl_platform.secure_aggregation.key_advertisement import (
    SecureCohortHandshakeError,
    generate_ephemeral_keypair,
    verify_frozen_cohort_roster,
)
from fl_platform.secure_aggregation.masked_update import (
    RosterContext,
    SecureCohortHandshakeMaskingError,
    encode_weighted_delta,
    mask_weighted_delta,
    validate_client_weight,
    validate_local_delta,
)
from fl_platform.secure_aggregation.user_level_clipping import clip_delta_to_l2_norm
from fl_platform.security import security_event as _security_event
from fl_platform.security.coordinator_task_verifier import CoordinatorTaskRejectedError
from fl_platform.worker import task_journal
from fl_platform.worker.cancellation import CancellationToken
from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    CoordinatorClient,
    CoordinatorRejectedError,
    CoordinatorUnavailableError,
    RunSpec,
)
from fl_platform.worker.task_runner import (
    TaskCancelled,
    TaskDeadlineExceeded,
    UnsupportedPrivacyCombinationError,
    build_bridge_compatible_model,
    run_local_training,
    run_private_local_training,
)

logger = logging.getLogger("fl_platform.worker")


@dataclass(slots=True)
class WorkerRunResult:
    tasks_completed: int = 0
    tasks_failed: int = 0
    heartbeat_failures: int = 0
    stopped_reason: str = ""


@dataclass(slots=True, frozen=True)
class SecureHandshakeResult:
    """Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    slice: what `_perform_secure_cohort_handshake` hands back to `run()`
    so the loop can encode, mask, and submit this task's local update
    instead of the ordinary cleartext path. `own_private_key_raw` is
    this task's fresh ephemeral X25519 private key -- session-scoped
    only, never persisted, dropped from scope once the masked update
    has been submitted or the task fails/aborts (see
    `_perform_secure_cohort_handshake`'s own docstring)."""

    roster: RosterContext
    own_private_key_raw: bytes
    max_client_weight: int
    max_absolute_update_bound: float
    # Secure User-Level Differential Privacy Runtime slice, Work Areas
    # E/G/H: mirrors the signed task binding's own secure_user_level_dp_*
    # fields -- clip_norm/effective_sensitivity default to 0.0 (a value
    # clip_delta_to_l2_norm already rejects, matching this dataclass's
    # existing "unset means inactive" convention) when
    # secure_user_level_dp_active is False.
    secure_user_level_dp_active: bool = False
    secure_user_level_clip_norm: float = 0.0
    secure_user_level_effective_sensitivity: float = 0.0


@dataclass(slots=True)
class WorkerLoopOptions:
    worker_id: str
    max_iterations: int | None = None  # None: run until shutdown/no-task; set for tests
    poll_interval_seconds: float = 0.0  # tests pass 0 to avoid real sleeping
    submission_retry_attempts: int = 3
    submission_retry_backoff_seconds: float = 0.5
    device: torch.device = field(default_factory=lambda: torch.device("cpu"))
    num_classes: int = 2
    in_channels: int = 1
    image_size: int = 4
    # Secure Cohort Handshake and Signed Roster Runtime slice
    # (docs/secure-cohort-handshake-foundation.md): how long to wait for
    # the coordinator to freeze the cohort and publish the signed
    # roster after this worker's own key advertisement is accepted --
    # bounded polling, never an unbounded wait. Tests pass interval=0 to
    # avoid real sleeping, same convention as poll_interval_seconds.
    secure_aggregation_roster_poll_attempts: int = 10
    secure_aggregation_roster_poll_interval_seconds: float = 1.0


class WorkerService:
    """Drives one worker's full lifecycle against a CoordinatorClient.

    Deliberately takes the CoordinatorClient and RunSpec as constructor
    arguments rather than owning transport selection itself — main.py (or
    a test) decides whether that client is the CLI bridge or a real gRPC
    client (see coordinator_client.py's module docstring for why both
    exist in this phase).
    """

    def __init__(
        self, client: CoordinatorClient, spec: RunSpec, options: WorkerLoopOptions
    ) -> None:
        self._client = client
        self._spec = spec
        self._options = options
        self._cancellation = CancellationToken()
        self._shutdown_requested = False
        # Sample-level DP is inherently per-client (see
        # fl_platform.privacy.accounting's module docstring); one
        # enforcer per client_id this worker process has trained,
        # reused across every task for that client so
        # STOP_AFTER_CURRENT_TASK's "refuse future tasks" is meaningful
        # within this process's lifetime. See docs/known-limitations.md
        # for why cross-worker-process continuity is not implemented.
        self._sample_budget_enforcers: dict[str, SampleBudgetEnforcer] = {}
        # Coordinator-Signed Tasks slice (docs/accepted-task-journal.md):
        # None unless the client was constructed with
        # trusted_coordinator_keys_path set (getattr, not an isinstance
        # check, since CliBridgeCoordinatorClient has no such attribute
        # at all -- duck-typed, matching CoordinatorClient's Protocol
        # style). When None, journal transitions below are all no-ops.
        self._task_journal: task_journal.AcceptedTaskJournal | None = getattr(
            client, "accepted_task_journal", None
        )

    def _journal_transition(self, task_id: str, status: str, now: float) -> None:
        if self._task_journal is not None:
            self._task_journal.transition(task_id, status, now)

    def request_shutdown(self, *_args: object) -> None:
        self._shutdown_requested = True

    def cancel_current_task(self) -> None:
        self._cancellation.cancel()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

    def register(self, now: float) -> None:
        try:
            self._client.register_worker(self._spec, self._options.worker_id, now)
        except CoordinatorUnavailableError:
            logger.error(
                "coordinator unavailable during registration; will retry next loop"
            )
            raise
        except CoordinatorRejectedError as error:
            logger.error("registration rejected by coordinator: %s", error)
            raise

    def _perform_secure_cohort_handshake(
        self, task: ClientTrainingTask
    ) -> SecureHandshakeResult | None:
        """Secure Cohort Handshake and Signed Roster Runtime slice
        (docs/secure-cohort-handshake-foundation.md), extended by the
        Masked Update Runtime and No-Dropout Secure FedAvg Finalization
        slice: when `task` binds to a live secure aggregation session,
        generates a fresh ephemeral X25519 keypair, advertises it,
        waits for the coordinator to freeze the complete cohort, and
        verifies the resulting signed roster -- reaching a verified
        READY_FOR_MASKED_TRAINING state. Raises
        SecureCohortHandshakeError on any failure (never returns a
        partial/best-effort result); the caller treats that exactly like
        a CoordinatorTaskRejectedError -- no training happens for this
        task. Returns None when this task is not secure-aggregation-bound
        (the caller proceeds with the ordinary unmasked path); otherwise
        returns the SecureHandshakeResult the caller needs to encode,
        mask, and submit -- this method itself never trains, encodes, or
        masks anything.

        The ephemeral private key is returned to the caller (not
        discarded here as in the prior slice, since masked training
        needs it) -- the caller is responsible for keeping it in
        session-scoped memory only and letting every reference to it go
        out of scope once this task's masked update has been submitted
        or the task has failed/aborted (never persisted, never reused
        across sessions -- a fresh session requires a fresh key,
        matching the threshold secret-sharing restriction's "fresh
        session required for retry" requirement).
        """
        binding = task.secure_aggregation
        if not binding.secure_aggregation_active:
            return None

        advertise = getattr(self._client, "advertise_secure_aggregation_key", None)
        fetch_roster = getattr(self._client, "get_frozen_cohort_roster", None)
        trusted_keys_fn = getattr(self._client, "trusted_coordinator_keys", None)
        if advertise is None or fetch_roster is None or trusted_keys_fn is None:
            raise SecureCohortHandshakeError(
                "this task's secure aggregation session is active, but the "
                "configured CoordinatorClient backend does not support the secure "
                "cohort handshake (requires GrpcCoordinatorClient with a "
                "signing_identity and trusted_coordinator_keys_path configured)"
            )
        trusted_keys = trusted_keys_fn()
        if not trusted_keys:
            raise SecureCohortHandshakeError(
                "cannot verify a frozen cohort roster without a trusted coordinator "
                "key bundle configured"
            )

        keypair = generate_ephemeral_keypair()
        outcome = advertise(
            self._spec, self._options.worker_id, task, keypair.public_key_raw
        )
        if not outcome.accepted:
            raise SecureCohortHandshakeError(
                f"key advertisement rejected: reason={outcome.reason!r} "
                f"rejection_reason={outcome.rejection_reason}"
            )

        roster = None
        attempts = max(1, self._options.secure_aggregation_roster_poll_attempts)
        for _ in range(attempts):
            roster_outcome = fetch_roster(self._options.worker_id, binding.session_id)
            if roster_outcome.available:
                roster = roster_outcome.roster
                break
            if self._options.secure_aggregation_roster_poll_interval_seconds > 0:
                time.sleep(self._options.secure_aggregation_roster_poll_interval_seconds)
        if roster is None:
            raise SecureCohortHandshakeError(
                f"frozen cohort roster for session '{binding.session_id}' was not "
                f"available after {attempts} poll attempts"
            )

        # Same "reject revoked/expired, otherwise proceed" convention as
        # verify_coordinator_task (coordinator_task_verifier.py) --
        # deliberately not an allow-list of every other status string.
        trusted_key = trusted_keys.get(roster.coordinator_signing_key_id)
        if trusted_key is None:
            raise SecureCohortHandshakeError(
                f"frozen roster's coordinator_signing_key_id "
                f"'{roster.coordinator_signing_key_id}' is not in the trusted "
                "coordinator key bundle"
            )
        if trusted_key.status == "revoked":
            raise SecureCohortHandshakeError(
                f"frozen roster was signed by a revoked coordinator key "
                f"'{roster.coordinator_signing_key_id}'"
            )
        if trusted_key.status == "expired":
            raise SecureCohortHandshakeError(
                f"frozen roster was signed by an expired coordinator key "
                f"'{roster.coordinator_signing_key_id}'"
            )

        verify_frozen_cohort_roster(
            roster,
            own_worker_id=self._options.worker_id,
            own_client_id=task.client_id,
            own_public_key_raw=keypair.public_key_raw,
            expected_session_id=binding.session_id,
            expected_run_id=self._spec.run_id,
            expected_round_id=task.round_id,
            expected_model_version=task.model_version,
            trusted_coordinator_public_key_hex=trusted_key.public_key_hex,
        )
        logger.info(
            "secure cohort handshake complete: client='%s' session_id='%s' -> "
            "READY_FOR_MASKED_TRAINING",
            task.client_id,
            binding.session_id,
        )

        peer_public_keys = {
            participant.worker_id: bytes.fromhex(
                participant.ephemeral_public_key_x25519
            )
            for participant in roster.participants
            if participant.worker_id != self._options.worker_id
        }
        roster_context = RosterContext(
            provider=roster.provider,
            protocol_version=roster.protocol_version,
            session_id=roster.session_id,
            run_id=roster.run_id,
            round_id=roster.round_id,
            model_version=roster.model_version,
            cohort_commitment=roster.cohort_commitment,
            tensor_manifest_hash=roster.tensor_manifest_hash,
            fixed_point_profile_hash=roster.fixed_point_profile_hash,
            cryptographic_profile_hash=roster.cryptographic_profile_hash,
            payload_hash=roster.payload_hash,
            peer_public_keys=peer_public_keys,
        )
        return SecureHandshakeResult(
            roster=roster_context,
            own_private_key_raw=keypair.private_key_raw,
            max_client_weight=binding.max_client_weight,
            max_absolute_update_bound=binding.max_absolute_update_bound,
            secure_user_level_dp_active=binding.secure_user_level_dp_active,
            secure_user_level_clip_norm=binding.secure_user_level_clip_norm,
            secure_user_level_effective_sensitivity=binding.secure_user_level_effective_sensitivity,
        )

    def _submit_with_retry(
        self,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None,
        sample_level_privacy: SampleLevelLedgerEntry | None = None,
        sample_privacy_decision: SampleBudgetDecision | None = None,
    ) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, self._options.submission_retry_attempts + 1):
            try:
                outcome = self._client.submit_result(
                    self._spec,
                    worker_id,
                    task,
                    delta,
                    sample_count,
                    update_id,
                    nonce,
                    now,
                    control_delta=control_delta,
                    refreshed_client_control_variate=refreshed_client_control_variate,
                    sample_level_privacy=sample_level_privacy,
                    sample_privacy_decision=sample_privacy_decision,
                )
                if not outcome.accepted:
                    logger.warning(
                        "result for client '%s' rejected: %s",
                        task.client_id,
                        outcome.reason,
                    )
                return outcome.accepted
            except CoordinatorUnavailableError as error:
                last_error = error
                logger.warning(
                    "submission attempt %d/%d failed (coordinator unavailable): %s",
                    attempt,
                    self._options.submission_retry_attempts,
                    error,
                )
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.submission_retry_backoff_seconds)
        if last_error is not None:
            logger.error(
                "submission for client '%s' failed after %d attempts",
                task.client_id,
                self._options.submission_retry_attempts,
            )
        return False

    def _encode_and_mask_local_update(
        self,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        handshake: SecureHandshakeResult,
    ) -> tuple[dict[str, list[int]], int, object]:
        """Work Areas E-J: local-update validation, bounded client-weight
        validation, fixed-point encoding, and pairwise tensor/weight
        masking -- the production wiring of the prior slice's tested
        pure-math library. Raises SecureCohortHandshakeMaskingError on
        any failure (never a partial/best-effort masked contribution).
        Returns (masked_tensors, masked_weight, encoding) -- `encoding`
        (a WeightedEncodingResult) is returned too since
        build_signed_masked_update needs its tensor_names/
        encoding_statistics.

        Uses the shared, hardcoded FixedPointEncodingProfile default
        (scale_factor=1048576.0, max_input_magnitude=100.0,
        max_client_weight=1_000_000, max_cohort_size=10_000,
        safety_margin=256) -- the exact same values
        CoordinatorServiceImpl::AcquireTask hardcodes when creating a
        session (coordinator_service.cpp), not independently
        configurable per session this slice. The roster's own
        `fixed_point_profile_hash` binds this same fixed profile on the
        coordinator side; a real per-session-configurable profile
        (with the worker deriving it from a real wire value rather than
        trusting a shared hardcoded default) is future work, disclosed
        in docs/known-limitations.md, not implemented this slice.
        """
        profile = FixedPointEncodingProfile()
        # Secure User-Level Differential Privacy Runtime slice, Work
        # Areas G/K: clipping happens BEFORE validate_local_delta's
        # per-element bound check -- an update whose raw values exceed
        # max_absolute_update_bound but whose L2 norm clips down to a
        # safe range must not be rejected before clipping ever runs.
        # Weight is forced to exactly 1 (never derived from
        # sample_count) per the Initial Weighting Restriction --
        # validate_client_weight is never called on this path.
        if handshake.secure_user_level_dp_active:
            clip_outcome = clip_delta_to_l2_norm(
                delta, handshake.secure_user_level_clip_norm
            )
            delta = clip_outcome.clipped_delta
            # Logs the configured (public, coordinator-known) clip
            # norm only -- never the pre-clip norm or the clipping
            # factor, both of which would reveal something about this
            # client's actual gradient magnitude (Work Area G: "No
            # value logging").
            logger.info(
                "secure user-level DP clipping applied: client='%s' clip_norm=%s",
                task.client_id,
                handshake.secure_user_level_clip_norm,
            )
            # Secure User-Level DP Operations, Observability, and Release
            # Evidence slice, Work Area D: real call-site wiring, not
            # just an enum definition. getattr-guarded because
            # CliBridgeCoordinatorClient (used by scripted/offline
            # tooling) has no _emit_security_event method -- matches
            # every other optional-method lookup on self._client in this
            # file (e.g. advertise_secure_aggregation_key above).
            emit_event = getattr(self._client, "_emit_security_event", None)
            if emit_event is not None:
                emit_event(
                    event_type=_security_event.EVENT_SECURE_USER_LEVEL_DP_CLIPPING_APPLIED,
                    subject_type=_security_event.SUBJECT_TYPE_SECURE_AGGREGATION_SESSION,
                    outcome=_security_event.OUTCOME_COMPLETED,
                    worker_id=self._options.worker_id,
                    task_id=task.task_id,
                )
            weight = 1
        else:
            weight = validate_client_weight(sample_count, handshake.max_client_weight)
        validate_local_delta(
            delta, max_absolute_update_bound=handshake.max_absolute_update_bound
        )
        encoding = encode_weighted_delta(delta, weight, profile)
        masked_tensors, masked_weight = mask_weighted_delta(
            encoding,
            self_worker_id=self._options.worker_id,
            self_private_key_raw=handshake.own_private_key_raw,
            roster=handshake.roster,
        )
        return masked_tensors, masked_weight, encoding

    def _submit_masked_with_retry(
        self,
        worker_id: str,
        task: ClientTrainingTask,
        masked_tensors: dict[str, list[int]],
        masked_weight: int,
        encoding: object,
        handshake: SecureHandshakeResult,
        sample_level_privacy: SampleLevelLedgerEntry | None = None,
        sample_privacy_decision: SampleBudgetDecision | None = None,
    ) -> bool:
        """Work Area N's worker-side counterpart: submits a real signed
        MaskedClientUpdate, retrying only on transport-level
        unavailability -- exactly the same retry policy
        `_submit_with_retry` already uses for the cleartext path (Work
        Area P: this is the ONLY submission path a secure-aggregation-
        bound task ever uses; there is no cleartext fallback anywhere
        in this method).

        Secure Hybrid Differential Privacy Runtime slice: when
        `sample_level_privacy` is not None, the real signed
        SignedSamplePrivacyRecord this task's own sample-level training
        already produced is submitted alongside the masked update
        (GrpcCoordinatorClient.submit_masked_update builds and signs it)
        -- closing the pre-existing sample_privacy_record_hash="" gap.
        """
        submit_masked = getattr(self._client, "submit_masked_update", None)
        if submit_masked is None:
            logger.error(
                "task for '%s' is secure-aggregation-bound but the configured "
                "CoordinatorClient backend does not support submit_masked_update",
                task.client_id,
            )
            return False
        last_error: Exception | None = None
        for attempt in range(1, self._options.submission_retry_attempts + 1):
            try:
                outcome = submit_masked(
                    self._spec,
                    worker_id,
                    task,
                    masked_tensors,
                    masked_weight,
                    encoding,
                    handshake.roster,
                    secure_user_level_dp_active=handshake.secure_user_level_dp_active,
                    clip_norm=handshake.secure_user_level_clip_norm,
                    effective_sensitivity=handshake.secure_user_level_effective_sensitivity,
                    sample_level_privacy=sample_level_privacy,
                    sample_privacy_decision=sample_privacy_decision,
                )
                if not outcome.accepted:
                    logger.warning(
                        "masked update for client '%s' rejected: reason=%r "
                        "rejection_reason=%s",
                        task.client_id,
                        outcome.reason,
                        outcome.rejection_reason,
                    )
                else:
                    logger.info(
                        "masked update accepted: client='%s' session_id='%s'",
                        task.client_id,
                        handshake.roster.session_id,
                    )
                return bool(outcome.accepted)
            except CoordinatorUnavailableError as error:
                last_error = error
                logger.warning(
                    "masked submission attempt %d/%d failed (coordinator "
                    "unavailable): %s",
                    attempt,
                    self._options.submission_retry_attempts,
                    error,
                )
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.submission_retry_backoff_seconds)
        if last_error is not None:
            logger.error(
                "masked submission for client '%s' failed after %d attempts",
                task.client_id,
                self._options.submission_retry_attempts,
            )
        return False

    def run(self) -> WorkerRunResult:
        result = WorkerRunResult()
        now = 0.0
        iteration = 0

        try:
            self.register(now)
        except (CoordinatorUnavailableError, CoordinatorRejectedError) as error:
            result.stopped_reason = f"registration failed: {error}"
            return result

        # Coordinator-Signed Tasks slice, Work Package O: any task left
        # PREPARING/TRAINING in a prior process's journal means that
        # process died mid-execution -- see task_journal.py's module
        # docstring for why "mark FAILED, require reissue" is the safe
        # policy here rather than silently resuming.
        if self._task_journal is not None:
            recovered = self._task_journal.recover_on_startup(now)
            for task_id in recovered:
                logger.warning(
                    "recovered an in-flight task from a prior process crash: "
                    "task_id=%s (marked FAILED; awaiting coordinator reissue)",
                    task_id,
                )

        while not self._shutdown_requested:
            if (
                self._options.max_iterations is not None
                and iteration >= self._options.max_iterations
            ):
                result.stopped_reason = "max_iterations reached"
                break
            iteration += 1

            try:
                task = self._client.acquire_task(
                    self._spec, self._options.worker_id, now
                )
            except CoordinatorUnavailableError as error:
                logger.warning(
                    "coordinator unavailable while acquiring a task: %s", error
                )
                result.heartbeat_failures += 1
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue
            except CoordinatorTaskRejectedError as error:
                # A coordinator-signed task failed verification (bad
                # signature, hash mismatch, replay, expiry, wrong
                # worker, or a detected duplicate execution) -- no model/
                # dataset access has happened, and none will: this is
                # treated as "no usable task this iteration," logged at
                # WARNING (not silently swallowed), never executed.
                logger.warning(
                    "coordinator-signed task rejected: reason=%s detail=%s",
                    error.reason.value,
                    error.detail,
                )
                result.tasks_failed += 1
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue
            except CoordinatorRejectedError as error:
                # Masked Update Runtime and No-Dropout Secure FedAvg
                # Finalization slice, Work Area B: a general gRPC
                # rejection from AcquireTask that is NOT a signed-task
                # verification failure (CoordinatorTaskRejectedError,
                # handled above) and not transport-level
                # (CoordinatorUnavailableError, handled above) -- e.g.
                # FAILED_PRECONDITION "unknown run_id" when this worker
                # process started before its configured run existed.
                # Previously uncaught here: propagated out of run() and
                # crashed the whole worker process instead of retrying
                # on the next poll, a real gap disclosed in
                # docs/known-limitations.md and confirmed live by the
                # prior slice's own Docker validation. Treated as
                # retryable, exactly like CoordinatorUnavailableError --
                # deliberately NOT treated as a permanent protocol
                # rejection: _grpc_call raises this exact class for a
                # broad "the coordinator said no" bucket that includes
                # genuinely transient conditions, not just permanent
                # ones. This does not weaken CoordinatorTaskRejectedError's
                # existing, stricter fail-closed handling above, which is
                # untouched -- a rejected signed task is still never
                # retried through this path.
                logger.warning(
                    "task acquisition rejected by coordinator (retryable): %s", error
                )
                result.tasks_failed += 1
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue

            if not task.has_task:
                if self._options.max_iterations is None:
                    result.stopped_reason = "no task available"
                    break
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue

            handshake: SecureHandshakeResult | None = None
            try:
                handshake = self._perform_secure_cohort_handshake(task)
            except SecureCohortHandshakeError as error:
                # Mirrors the CoordinatorTaskRejectedError handling
                # above: no model/dataset access has happened, and none
                # will -- this task is skipped, never partially executed.
                logger.warning(
                    "secure cohort handshake failed for client '%s': %s",
                    task.client_id,
                    error,
                )
                result.tasks_failed += 1
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue

            self._journal_transition(task.task_id, task_journal.PREPARING, now)
            self._cancellation.reset()
            model = build_bridge_compatible_model(
                num_classes=self._options.num_classes,
                in_channels=self._options.in_channels,
                image_size=self._options.image_size,
            )
            global_state = {
                name: tensor.clone() for name, tensor in model.state_dict().items()
            }

            sample_level_privacy: SampleLevelLedgerEntry | None = None
            sample_privacy_decision: SampleBudgetDecision | None = None
            self._journal_transition(task.task_id, task_journal.TRAINING, now)
            try:
                if task.sample_level_dp_active:
                    # Never silently fall back to non-private training:
                    # a missing sample_level_privacy config here is a
                    # coordinator/worker contract bug, not something to
                    # paper over — let it raise.
                    assert task.sample_level_privacy is not None
                    enforcer = self._sample_budget_enforcers.get(task.client_id)
                    if enforcer is None or (
                        enforcer.epsilon_budget
                        != task.sample_level_privacy.epsilon_budget
                    ):
                        # Fresh enforcer per client, or a reconfigured
                        # budget for an already-seen client (a changed
                        # budget starts fresh enforcement, never silently
                        # keeps enforcing a stale value).
                        enforcer = SampleBudgetEnforcer(
                            client_id=task.client_id,
                            policy=task.sample_level_privacy.sample_budget_policy,
                            epsilon_budget=task.sample_level_privacy.epsilon_budget,
                            target_delta=task.sample_level_privacy.target_delta,
                        )
                        self._sample_budget_enforcers[task.client_id] = enforcer
                    # No silent continuation after hard exhaustion: a
                    # client already stopped by a prior task's
                    # STOP_AFTER_CURRENT_TASK/FAIL_TASK decision is
                    # refused before any new training starts.
                    enforcer.refuse_if_already_stopped()
                    outcome, privacy_result = run_private_local_training(
                        task,
                        global_state,
                        model,
                        task.sample_level_privacy,
                        device=self._options.device,
                        seed=hash(task.client_id) & 0xFFFF,
                        num_classes=self._options.num_classes,
                        in_channels=self._options.in_channels,
                        image_size=self._options.image_size,
                        is_cancelled=self._cancellation.is_cancelled,
                        budget_enforcer=enforcer,
                    )
                    sample_level_privacy = SampleLevelLedgerEntry(
                        run_id=self._spec.run_id,
                        round_id=task.round_id,
                        client_id=task.client_id,
                        epsilon=privacy_result.epsilon,
                        delta=privacy_result.delta,
                        noise_multiplier=privacy_result.noise_multiplier,
                        sample_rate=privacy_result.sample_rate,
                        steps=privacy_result.steps,
                        accountant=privacy_result.accountant,
                        recorded_at=datetime.now(UTC).isoformat(),
                        entry_id=str(uuid.uuid4()),
                    )
                    # Real decision from the enforcer that actually
                    # governed this step (run_private_local_training
                    # calls budget_enforcer.check_after_step internally)
                    # -- never independently re-derived here. Signed
                    # into the privacy record's budget_decision/
                    # accountant_state_hash fields by
                    # GrpcCoordinatorClient when a signing identity is
                    # configured -- see docs/signed-privacy-records.md.
                    sample_privacy_decision = enforcer.last_decision
                    record_sample_level_training_success(
                        self._spec.run_id, task.client_id, privacy_result.epsilon
                    )
                else:
                    outcome = run_local_training(
                        task,
                        global_state,
                        model,
                        device=self._options.device,
                        seed=hash(task.client_id) & 0xFFFF,
                        num_classes=self._options.num_classes,
                        in_channels=self._options.in_channels,
                        image_size=self._options.image_size,
                        is_cancelled=self._cancellation.is_cancelled,
                    )
            except UnsupportedPrivacyCombinationError as error:
                logger.error(
                    "task for '%s' requested an unsupported privacy combination: %s",
                    task.client_id,
                    error,
                )
                record_sample_level_training_rejected()
                self._journal_transition(task.task_id, task_journal.FAILED, now)
                result.tasks_failed += 1
                continue
            except SecureRandomTaskRejectedError as error:
                # Never silently downgrade to secure_mode=False -- see
                # fl_platform.privacy.secure_random.require_opacus_secure_mode.
                logger.error(
                    "task for '%s' rejected: secure-random mode unavailable: %s",
                    task.client_id,
                    error,
                )
                record_sample_level_training_rejected()
                self._journal_transition(task.task_id, task_journal.FAILED, now)
                result.tasks_failed += 1
                continue
            except SampleLevelBudgetExceededError as error:
                # Structured task-failure reason (error.decision), never
                # a silent continuation past hard budget exhaustion — no
                # update is submitted. See
                # fl_platform.privacy.budget_enforcement.
                logger.warning(
                    "task for '%s' blocked by sample-level privacy budget "
                    "enforcement: outcome=%s policy=%s epsilon=%.6f budget=%.6f "
                    "reason=%s",
                    task.client_id,
                    error.decision.outcome.value,
                    error.decision.policy.value,
                    error.decision.current_epsilon,
                    error.decision.budget,
                    error.decision.reason,
                )
                record_sample_level_training_rejected()
                self._journal_transition(task.task_id, task_journal.FAILED, now)
                result.tasks_failed += 1
                continue
            except TaskCancelled as error:
                logger.info("task for '%s' cancelled: %s", task.client_id, error)
                self._journal_transition(task.task_id, task_journal.CANCELED, now)
                result.tasks_failed += 1
                continue
            except TaskDeadlineExceeded as error:
                logger.warning(
                    "task for '%s' missed its deadline: %s", task.client_id, error
                )
                self._journal_transition(task.task_id, task_journal.FAILED, now)
                result.tasks_failed += 1
                continue
            except RuntimeError as error:
                # Covers CUDA-unavailable / out-of-memory / other torch
                # runtime failures: log and move on rather than crash the
                # whole worker process over one bad task.
                logger.exception(
                    "training exception for client '%s': %s", task.client_id, error
                )
                self._journal_transition(task.task_id, task_journal.FAILED, now)
                result.tasks_failed += 1
                continue

            self._journal_transition(task.task_id, task_journal.RESULT_READY, now)

            if handshake is not None:
                # Work Area P: the ONLY submission path a secure-
                # aggregation-bound task ever reaches -- there is no
                # cleartext fallback anywhere below, structurally (not
                # merely by convention): _submit_with_retry (the
                # cleartext ClientResult path) is never called on this
                # branch, not even on a masking/encoding failure.
                try:
                    masked_tensors, masked_weight, encoding = (
                        self._encode_and_mask_local_update(
                            task, outcome.delta, outcome.sample_count, handshake
                        )
                    )
                except SecureCohortHandshakeMaskingError as error:
                    logger.warning(
                        "local update encoding/masking failed for client '%s': %s",
                        task.client_id,
                        error,
                    )
                    self._journal_transition(task.task_id, task_journal.FAILED, now)
                    result.tasks_failed += 1
                    continue
                accepted = self._submit_masked_with_retry(
                    self._options.worker_id,
                    task,
                    masked_tensors,
                    masked_weight,
                    encoding,
                    handshake,
                    sample_level_privacy=sample_level_privacy,
                    sample_privacy_decision=sample_privacy_decision,
                )
            else:
                accepted = self._submit_with_retry(
                    self._options.worker_id,
                    task,
                    outcome.delta,
                    outcome.sample_count,
                    update_id=f"update-{task.client_id}-{task.round_id}",
                    nonce=f"nonce-{task.client_id}-{task.round_id}",
                    now=now,
                    control_delta=outcome.control_delta,
                    refreshed_client_control_variate=outcome.refreshed_client_control_variate,
                    sample_level_privacy=sample_level_privacy,
                    sample_privacy_decision=sample_privacy_decision,
                )
            if accepted:
                self._journal_transition(
                    task.task_id, task_journal.RESULT_SUBMITTED, now
                )
                self._journal_transition(task.task_id, task_journal.COMPLETED, now)
                result.tasks_completed += 1
            else:
                self._journal_transition(task.task_id, task_journal.FAILED, now)
                result.tasks_failed += 1

        if not result.stopped_reason:
            result.stopped_reason = (
                "shutdown requested" if self._shutdown_requested else "unknown"
            )
        return result
