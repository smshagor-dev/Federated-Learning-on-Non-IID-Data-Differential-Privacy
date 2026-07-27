"""Masked Update Runtime and No-Dropout Secure FedAvg Finalization slice
(docs/secure-aggregation-masked-update.md). The worker-side counterpart
to cpp/coordinator/src/coordinator_service.cpp's SubmitMaskedClientUpdate
handler: local-update validation, bounded-integer client weighting,
weighted fixed-point encoding, pairwise tensor/weight masking, and
signed MaskedClientUpdate construction. Reuses the prior slice's real,
tested pure-math library
(fl_platform.secure_aggregation.{crypto,fixed_point_encoding,
pairwise_mask,tensor_mask}) directly -- no math is re-derived here, only
wired into a real per-tensor/per-peer production flow for the first
time. See docs/secure-aggregation-masked-runtime-audit.md for the
canonical-mask-context design and the settled weighting order.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import torch

# HKDF purpose labels, reused verbatim from the pure-math library's own
# already-established naming (crypto.py) -- not reinvented under a
# different name, per this slice's "do not rename existing approved
# responsibility-based components" instruction.
from fl_platform.secure_aggregation.crypto import (  # noqa: E402 -- grouped with the other purpose-label import for clarity
    HKDF_PURPOSE_TENSOR_MASK_STREAM,
    HKDF_PURPOSE_WEIGHT_MASK_STREAM,
    compute_masked_values_checksum,
    derive_x25519_shared_secret,
)
from fl_platform.secure_aggregation.crypto import (
    sha256_hex as envelope_sha256_hex,
)
from fl_platform.secure_aggregation.fixed_point_encoding import (
    UINT64_MASK,
    FixedPointEncodingProfile,
    encode_value,
)
from fl_platform.secure_aggregation.pairwise_mask import (
    SignedMask,
    mask_encoded_value,
    resolve_pairwise_mask_sign,
)
from fl_platform.secure_aggregation.tensor_mask import (
    PeerMaskStream,
    derive_tensor_mask_stream,
    derive_weight_mask,
    mask_tensor,
)
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_SECURE_AGGREGATION,
    MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE,
    EncodingStatisticsFields,
    EnvelopeFields,
    MaskedClientUpdateFields,
    MaskedTensorFields,
    SignedEnvelope,
    masked_client_update_payload_hash_input,
    sign_envelope,
)
from fl_platform.security.signing_identity import WorkerSigningIdentity


class SecureCohortHandshakeMaskingError(RuntimeError):
    """Raised on any masked-execution failure in this module -- local-
    update validation, client-weight validation, encoding, or masking.
    Handled by WorkerService exactly like SecureCohortHandshakeError
    (fl_platform.secure_aggregation.key_advertisement): the current
    secure task fails safely, no partial/best-effort submission is ever
    attempted."""


class SecureTaskState(enum.Enum):
    """Work Area D's explicit worker masked-execution state machine.
    Deliberately a single linear chain (no branching states to
    validate beyond "is this transition allowed from the current one")
    -- the task specification's own list is already exactly that: one
    ordered sequence with three terminal outcomes. Not a full state-
    machine class mirroring CohortStateMachine's C++ complexity, since
    nothing here needs concurrent-transition validation (one task, one
    worker, one thread)."""

    SECURE_TASK_VERIFIED = "SECURE_TASK_VERIFIED"
    EPHEMERAL_KEY_CREATED = "EPHEMERAL_KEY_CREATED"
    KEY_ADVERTISEMENT_ACCEPTED = "KEY_ADVERTISEMENT_ACCEPTED"
    FROZEN_ROSTER_VERIFIED = "FROZEN_ROSTER_VERIFIED"
    READY_FOR_MASKED_TRAINING = "READY_FOR_MASKED_TRAINING"
    LOCAL_TRAINING = "LOCAL_TRAINING"
    LOCAL_UPDATE_VALIDATED = "LOCAL_UPDATE_VALIDATED"
    FIXED_POINT_ENCODED = "FIXED_POINT_ENCODED"
    MASKED_UPDATE_CREATED = "MASKED_UPDATE_CREATED"
    MASKED_UPDATE_SUBMITTED = "MASKED_UPDATE_SUBMITTED"
    SECURE_TASK_COMPLETED = "SECURE_TASK_COMPLETED"
    SECURE_TASK_ABORTED = "SECURE_TASK_ABORTED"
    SECURE_TASK_FAILED = "SECURE_TASK_FAILED"


# The single allowed forward chain -- mirrors
# cpp/coordinator/src/secure_aggregation_session.cpp's
# is_allowed_forward_transition's own "flat, explicit allow-list, never
# implicitly reachable" design philosophy. Every non-terminal state may
# also move directly to SECURE_TASK_ABORTED or SECURE_TASK_FAILED (a
# session abort / an unexpected local failure can happen from anywhere
# in the sequence), handled separately in validate_transition below
# rather than duplicated into this chain.
_FORWARD_CHAIN: tuple[SecureTaskState, ...] = (
    SecureTaskState.SECURE_TASK_VERIFIED,
    SecureTaskState.EPHEMERAL_KEY_CREATED,
    SecureTaskState.KEY_ADVERTISEMENT_ACCEPTED,
    SecureTaskState.FROZEN_ROSTER_VERIFIED,
    SecureTaskState.READY_FOR_MASKED_TRAINING,
    SecureTaskState.LOCAL_TRAINING,
    SecureTaskState.LOCAL_UPDATE_VALIDATED,
    SecureTaskState.FIXED_POINT_ENCODED,
    SecureTaskState.MASKED_UPDATE_CREATED,
    SecureTaskState.MASKED_UPDATE_SUBMITTED,
    SecureTaskState.SECURE_TASK_COMPLETED,
)
_TERMINAL_STATES = frozenset(
    {
        SecureTaskState.SECURE_TASK_COMPLETED,
        SecureTaskState.SECURE_TASK_ABORTED,
        SecureTaskState.SECURE_TASK_FAILED,
    }
)


def validate_transition(
    current: SecureTaskState | None, target: SecureTaskState
) -> None:
    """Raises SecureCohortHandshakeMaskingError on an out-of-order
    transition. current=None means "no state yet" (only
    SECURE_TASK_VERIFIED is a valid first transition)."""
    if current is not None and current in _TERMINAL_STATES:
        raise SecureCohortHandshakeMaskingError(
            f"cannot transition out of terminal state {current.value} to {target.value}"
        )
    if target in (
        SecureTaskState.SECURE_TASK_ABORTED,
        SecureTaskState.SECURE_TASK_FAILED,
    ):
        return  # always reachable from any non-terminal state
    if current is None:
        if target is not SecureTaskState.SECURE_TASK_VERIFIED:
            raise SecureCohortHandshakeMaskingError(
                "the first masked-execution state must be SECURE_TASK_VERIFIED, "
                f"not {target.value}"
            )
        return
    current_index = _FORWARD_CHAIN.index(current)
    target_index = _FORWARD_CHAIN.index(target) if target in _FORWARD_CHAIN else -1
    if target_index != current_index + 1:
        raise SecureCohortHandshakeMaskingError(
            f"illegal masked-execution state transition: "
            f"{current.value} -> {target.value}"
        )


def validate_client_weight(sample_count: int, max_client_weight: int) -> int:
    """Work Area F: the existing FedAvg weighting source (number of
    local examples -- run_local_training's/run_private_local_training's
    TrainingOutcome.sample_count, the same field the non-secure path
    already uses) converted to a bounded positive integer. Documented
    honest-client limitation: a malicious worker may misreport its own
    sample_count (no attestation or verifiable counting exists in this
    provider) -- this function only bounds the value this worker
    itself reports, it cannot detect a lie."""
    weight = int(sample_count)
    if weight <= 0:
        raise SecureCohortHandshakeMaskingError(
            f"client weight must be a positive integer, got {sample_count!r}"
        )
    if max_client_weight > 0 and weight > max_client_weight:
        raise SecureCohortHandshakeMaskingError(
            f"client weight {weight} exceeds the signed task's max_client_weight "
            f"({max_client_weight})"
        )
    return weight


def validate_local_delta(
    delta: dict[str, torch.Tensor], *, max_absolute_update_bound: float
) -> None:
    """Work Area E, steps 9-12: canonical tensor-name validation is the
    caller's responsibility (this function only validates values, not
    names/shapes against a manifest -- see this module's own module
    docstring and the audit doc's note that this manager has no
    independently-sourced ModelManifest this pass, mirrored here on the
    worker side for the same reason). Rejects non-finite values and any
    value exceeding the signed task's own bound. Never logs a tensor
    value -- only shapes/names ever appear in a raised message."""
    for name, tensor in delta.items():
        if not torch.isfinite(tensor).all():
            raise SecureCohortHandshakeMaskingError(
                f"local delta tensor {name!r} contains a non-finite value -- "
                "refusing to encode"
            )
        max_abs = tensor.abs().max().item() if tensor.numel() > 0 else 0.0
        if max_absolute_update_bound > 0.0 and max_abs > max_absolute_update_bound:
            raise SecureCohortHandshakeMaskingError(
                f"local delta tensor {name!r} exceeds the signed task's "
                f"max_absolute_update_bound ({max_absolute_update_bound}) -- refusing "
                "to encode"
            )


@dataclass(slots=True, frozen=True)
class WeightedEncodingResult:
    """Work Area G/H's output: every tensor's canonically-ordered,
    weighted-then-quantized ring values, plus the weight's own encoded
    ring value and aggregate quantization statistics."""

    tensor_names: tuple[str, ...]  # canonical (sorted) order, matches masked_tensors'
    encoded_tensors: dict[str, list[int]]
    encoded_weight: int
    total_elements: int
    max_quantization_error: float
    mean_quantization_error: float


def encode_weighted_delta(
    delta: dict[str, torch.Tensor], weight: int, profile: FixedPointEncodingProfile
) -> WeightedEncodingResult:
    """Work Area G/H: **encode(delta) then multiply by integer_weight in
    the ring** -- `(encode_value(v, profile).encoded * weight) &
    UINT64_MASK`, not `encode_value(v * weight, profile)`.

    This is a considered correction of this module's own earlier design
    note (docs/secure-aggregation-masked-runtime-audit.md originally
    read the C++ capstone test's `encode_value(v * weight, profile)`
    call as "the settled order" -- live-tested against realistic
    per-element magnitudes together with a realistic sample-count
    weight, that combination overflows `max_input_magnitude` long
    before any real overflow risk exists in the ring itself). The
    session's own domain-bounds safety proof
    (`prove_domain_bounds`/`DomainBoundsProof`, fixed_point_encoding.py)
    is unambiguous about which order it assumes: it computes
    `worst_case_single_encoded = max_input_magnitude * scale_factor`
    (the bound on one RAW, PRE-WEIGHT encoded element) and *separately*
    multiplies that by `max_client_weight` and then `max_cohort_size` to
    prove the ring never wraps. Calling `encode_value(v * weight, ...)`
    checks the *already-weighted* product against `max_input_magnitude`
    -- a bound the proof never intended to apply there -- which is
    merely coincidentally harmless for the capstone test's own small,
    hand-picked weights (10/20/5), not correct in general. Encoding the
    raw value first and multiplying by weight afterward in ring space
    matches the proof's own accounting exactly and is mathematically
    equivalent (real-number multiplication commutes; the two orders
    only differ in exactly where rounding happens, a quantization-error
    concern already tracked by `encoding_statistics`, not a magnitude-
    overflow one) -- finalize() (C++) still just sums ring values and
    divides by the separately-summed decoded weight, unaffected by
    which side performed this multiplication.
    """
    encoded_tensors: dict[str, list[int]] = {}
    total_elements = 0
    max_error = 0.0
    error_sum = 0.0
    for name in sorted(delta.keys()):
        tensor = delta[name]
        flat = tensor.detach().to(torch.float64).flatten().tolist()
        values: list[int] = []
        for v in flat:
            result = encode_value(v, profile)
            if not result.ok:
                raise SecureCohortHandshakeMaskingError(
                    f"failed to fixed-point encode tensor {name!r}: "
                    f"reason={result.reason}"
                )
            weighted_ring_value = (result.encoded * weight) & UINT64_MASK
            values.append(weighted_ring_value)
            total_elements += 1
            error_sum += result.quantization_error
            max_error = max(max_error, result.quantization_error)
        encoded_tensors[name] = values
    weight_result = encode_value(float(weight), profile)
    if not weight_result.ok:
        raise SecureCohortHandshakeMaskingError(
            "failed to fixed-point encode the client weight: "
            f"reason={weight_result.reason}"
        )
    mean_error = error_sum / total_elements if total_elements > 0 else 0.0
    return WeightedEncodingResult(
        tensor_names=tuple(sorted(delta.keys())),
        encoded_tensors=encoded_tensors,
        encoded_weight=weight_result.encoded,
        total_elements=total_elements,
        max_quantization_error=max_error,
        mean_quantization_error=mean_error,
    )


def canonical_mask_context(
    *,
    provider: int,
    protocol_version: int,
    session_id: str,
    run_id: str,
    round_id: int,
    model_version: str,
    cohort_commitment: str,
    self_participant_id: str,
    peer_participant_id: str,
    tensor_name: str,
    chunk_index: int = 0,
) -> str:
    """Work Area I/J: the canonical binding string this slice defines
    (no prior caller existed -- both derive_purpose_key's and
    derive_tensor_mask_stream's own doc comments say constructing this
    is the caller's responsibility). Byte-identical on both sides of a
    pairwise derivation regardless of which side computes it --
    participant_low/participant_high (not "self"/"peer"), since both
    workers in a pair must derive the exact same shared secret and the
    exact same mask value; resolve_pairwise_mask_sign (unchanged) is
    what gives each side the correct *sign* to apply against that same
    shared mask. tensor_name is empty for a weight mask -- purpose_label
    (passed separately to derive_purpose_key, not part of this string)
    already provides domain separation between tensor and weight masks,
    so tensor_name="" is not ambiguous with any real tensor's context.
    chunk_index is always 0 this slice -- tensors are masked whole, not
    split into bounded sub-chunks (see the audit doc's explicit
    reasoning)."""
    low, high = sorted((self_participant_id, peer_participant_id))
    return (
        f"provider={int(provider)}\x1e"
        f"protocol_version={protocol_version}\x1e"
        f"session_id={session_id}\x1e"
        f"run_id={run_id}\x1e"
        f"round_id={round_id}\x1e"
        f"model_version={model_version}\x1e"
        f"cohort_commitment={cohort_commitment}\x1e"
        f"participant_low={low}\x1e"
        f"participant_high={high}\x1e"
        f"tensor_name={tensor_name}\x1e"
        f"chunk_index={chunk_index}"
    )


@dataclass(slots=True, frozen=True)
class RosterContext:
    """The subset of a verified FrozenCohortRoster this module needs to
    mask against -- duck-typed the same way
    fl_platform.secure_aggregation.key_advertisement.FrozenCohortRosterLike
    is, so this module has no import-time dependency on generated
    protobuf bindings either. The caller (WorkerService) builds this
    from the real, already-verified roster after
    verify_frozen_cohort_roster succeeds."""

    provider: int
    protocol_version: int
    session_id: str
    run_id: str
    round_id: int
    model_version: str
    cohort_commitment: str
    tensor_manifest_hash: str
    fixed_point_profile_hash: str
    cryptographic_profile_hash: str
    payload_hash: str
    # worker_id -> raw 32-byte X25519 public key, excludes self
    peer_public_keys: dict[str, bytes]


def mask_weighted_delta(
    encoding: WeightedEncodingResult,
    *,
    self_worker_id: str,
    self_private_key_raw: bytes,
    roster: RosterContext,
) -> tuple[dict[str, list[int]], int]:
    """Work Area I/J: for every peer in the frozen roster, derives the
    pairwise shared secret, then a purpose-specific mask stream per
    tensor (and a separate purpose-specific single mask value for the
    weight), and folds every peer's contribution into this worker's own
    encoded values using the canonical sign rule
    (resolve_pairwise_mask_sign, unchanged). Returns
    (masked_tensors_by_name, masked_weight). Raises
    SecureCohortHandshakeMaskingError on an all-zero/invalid peer
    public key (via derive_x25519_shared_secret's own existing
    rejection) or a length mismatch (via mask_tensor's own existing
    rejection) -- never silently proceeds with a degenerate mask.

    Deliberately does not delete self_private_key_raw's bytes in place
    (Python strings/bytes are immutable -- there is no safe in-place
    zeroization primitive for a `bytes` object without a C-extension
    this project does not depend on); the caller is responsible for
    dropping every reference to it once this function returns, so it
    becomes eligible for garbage collection promptly (see
    WorkerService's own session-scoped-only handling, matching
    EphemeralKeyPair's existing documented convention)."""
    tensor_peer_streams: dict[str, list[PeerMaskStream]] = {
        name: [] for name in encoding.tensor_names
    }
    weight_signed_masks: list[SignedMask] = []

    for peer_worker_id, peer_public_key in sorted(roster.peer_public_keys.items()):
        if peer_worker_id == self_worker_id:
            continue
        shared_secret = derive_x25519_shared_secret(
            self_private_key_raw, peer_public_key
        )
        sign = resolve_pairwise_mask_sign(self_worker_id, peer_worker_id)

        for tensor_name in encoding.tensor_names:
            element_count = len(encoding.encoded_tensors[tensor_name])
            context = canonical_mask_context(
                provider=roster.provider,
                protocol_version=roster.protocol_version,
                session_id=roster.session_id,
                run_id=roster.run_id,
                round_id=roster.round_id,
                model_version=roster.model_version,
                cohort_commitment=roster.cohort_commitment,
                self_participant_id=self_worker_id,
                peer_participant_id=peer_worker_id,
                tensor_name=tensor_name,
            )
            stream = derive_tensor_mask_stream(
                shared_secret, HKDF_PURPOSE_TENSOR_MASK_STREAM, context, element_count
            )
            tensor_peer_streams[tensor_name].append(
                PeerMaskStream(
                    peer_participant_id=peer_worker_id, sign=sign, mask_values=stream
                )
            )

        weight_context = canonical_mask_context(
            provider=roster.provider,
            protocol_version=roster.protocol_version,
            session_id=roster.session_id,
            run_id=roster.run_id,
            round_id=roster.round_id,
            model_version=roster.model_version,
            cohort_commitment=roster.cohort_commitment,
            self_participant_id=self_worker_id,
            peer_participant_id=peer_worker_id,
            tensor_name="",
        )
        weight_mask = derive_weight_mask(
            shared_secret, HKDF_PURPOSE_WEIGHT_MASK_STREAM, weight_context
        )
        weight_signed_masks.append(SignedMask(mask=weight_mask, sign=sign))

    masked_tensors = {
        name: mask_tensor(encoding.encoded_tensors[name], tensor_peer_streams[name])
        for name in encoding.tensor_names
    }
    masked_weight = mask_encoded_value(encoding.encoded_weight, weight_signed_masks)
    return masked_tensors, masked_weight


def build_signed_masked_update(
    *,
    masked_tensors: dict[str, list[int]],
    masked_weight: int,
    encoding: WeightedEncodingResult,
    roster: RosterContext,
    task_id: str,
    lease_id: str,
    attempt: int,
    worker_id: str,
    client_id: str,
    sample_privacy_record_hash: str,
    signing_identity: WorkerSigningIdentity,
    sequence_number: int,
    nonce: str,
    issued_at: float | None = None,
    expires_at_seconds_from_now: float = 300.0,
) -> tuple[MaskedClientUpdateFields, SignedEnvelope]:
    """Work Areas K/L/M: builds a real MaskedClientUpdate payload (every
    field checksummed/hashed for real, never a clear tensor value
    anywhere), computes its canonical payload hash, and signs it with
    the worker's real Ed25519 WorkerSigningIdentity, wrapped in a
    SignedWorkerEnvelope (message_type =
    MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE, message_stream =
    MESSAGE_STREAM_SECURE_AGGREGATION -- the same stream key
    advertisements use, per that message type's own proto comment)."""
    import time as _time

    now = issued_at if issued_at is not None else _time.time()
    expires_at = now + expires_at_seconds_from_now

    masked_tensor_fields = tuple(
        MaskedTensorFields(
            tensor_name=name,
            masked_values=tuple(masked_tensors[name]),
            checksum=compute_masked_values_checksum(masked_tensors[name]),
        )
        for name in encoding.tensor_names
    )
    masked_weight_checksum = compute_masked_values_checksum([masked_weight])

    update_fields = MaskedClientUpdateFields(
        provider=roster.provider,
        protocol_version=roster.protocol_version,
        session_id=roster.session_id,
        run_id=roster.run_id,
        round_id=roster.round_id,
        task_id=task_id,
        lease_id=lease_id,
        attempt=attempt,
        worker_id=worker_id,
        client_id=client_id,
        model_version=roster.model_version,
        cohort_commitment=roster.cohort_commitment,
        tensor_manifest_hash=roster.tensor_manifest_hash,
        fixed_point_profile_hash=roster.fixed_point_profile_hash,
        frozen_roster_payload_hash=roster.payload_hash,
        cryptographic_profile_hash=roster.cryptographic_profile_hash,
        masked_tensors=masked_tensor_fields,
        masked_weight=masked_weight,
        masked_weight_checksum=masked_weight_checksum,
        encoding_statistics=EncodingStatisticsFields(
            total_elements=encoding.total_elements,
            max_quantization_error=encoding.max_quantization_error,
            mean_quantization_error=encoding.mean_quantization_error,
        ),
        sample_privacy_record_hash=sample_privacy_record_hash,
        issued_at=now,
        expires_at=expires_at,
    )
    payload_hash = envelope_sha256_hex(
        masked_client_update_payload_hash_input(update_fields).encode("utf-8")
    )
    envelope_fields = EnvelopeFields(
        message_type=MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE,
        worker_id=worker_id,
        run_id=roster.run_id,
        round_id=roster.round_id,
        task_id=task_id,
        client_id=client_id,
        model_version=roster.model_version,
        message_stream=MESSAGE_STREAM_SECURE_AGGREGATION,
        sequence_number=sequence_number,
        signing_key_id=signing_identity.key_id,
        payload_hash=payload_hash,
        issued_at=now,
        expires_at=expires_at,
        nonce=nonce,
    )
    signed_envelope = sign_envelope(envelope_fields, signing_identity)
    return update_fields, signed_envelope


__all__ = [
    "RosterContext",
    "SecureCohortHandshakeMaskingError",
    "SecureTaskState",
    "WeightedEncodingResult",
    "build_signed_masked_update",
    "canonical_mask_context",
    "encode_weighted_delta",
    "mask_weighted_delta",
    "validate_client_weight",
    "validate_local_delta",
    "validate_transition",
]
