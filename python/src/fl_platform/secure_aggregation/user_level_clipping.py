"""Worker-side deterministic global L2 clipping for secure user-level
differential privacy -- Secure User-Level Differential Privacy Runtime
slice, Work Areas F/G/H. See docs/secure-user-level-dp-semantics.md
for the full mechanism specification this module implements (sections
2-4, 11) and docs/secure-user-level-dp-runtime-audit.md for why this
is a NEW, worker-side path rather than a reuse of the existing
coordinator-side ``fl_core::clip_client_delta`` (which operates on
cleartext per-client deltas the coordinator collects -- structurally
incompatible with secure aggregation's "coordinator never sees an
individual update" guarantee).

This module never logs, persists, or returns to any RPC caller the
unclipped norm or the clipping factor -- both are computed and used
purely locally (Work Area G: "No value logging", "No clear update
persistence"). The only thing that ever leaves the worker process
about clipping is: the clipped, fixed-point-encoded, pairwise-masked
update itself (via the existing masked_update.py pipeline), and a
signed attestation (user_level_attestation.py) that deliberately
excludes the unclipped norm, the clipped norm, and the clipping factor
-- see that module's own docstring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from fl_platform.secure_aggregation.fixed_point_encoding import (
    FixedPointEncodingProfile,
)


class UserLevelClippingError(RuntimeError):
    """Raised when a delta cannot be safely clipped -- non-finite
    values, an empty update, or a non-positive clip norm. Never raised
    for an oversized update (that is exactly what clipping handles) or
    a zero-norm update (clipping is a no-op scale of 1.0 there)."""


def compute_global_l2_norm(delta: dict[str, torch.Tensor]) -> float:
    """Deterministic whole-update global L2 norm -- Work Area F.

    Canonical tensor order: sorted by tensor name (matches
    encode_weighted_delta's own sorted-name convention elsewhere in
    this package). Canonical element order: each tensor's own
    row-major flatten() order, which is deterministic for a fixed
    shape regardless of architecture (PyTorch tensors do not reorder
    elements based on CPU/accelerator). Accumulation is in Python
    float (IEEE-754 double, i.e. float64) via a running sum of squared
    element magnitudes -- summed across every tensor before the single
    final sqrt, not per-tensor sqrt-then-combined (which would compute
    a different, incorrect norm). Secure aggregation is FedAvg-only
    this slice, so every tensor in ``delta`` is a shared/aggregatable
    parameter -- there is no personalized/local-only parameter to
    exclude (Work Area F's exclusion requirement is satisfied
    vacuously, not silently skipped; see the semantics doc section 4).

    Raises UserLevelClippingError on an empty delta or any non-finite
    element -- clipping must never silently treat NaN/Inf as zero.
    """
    if not delta:
        raise UserLevelClippingError(
            "cannot compute a global L2 norm over an empty update"
        )
    sum_of_squares = 0.0
    for tensor_name in sorted(delta.keys()):
        tensor = delta[tensor_name]
        flat = tensor.detach().to(dtype=torch.float64).flatten()
        if not torch.isfinite(flat).all():
            raise UserLevelClippingError(
                f"tensor '{tensor_name}' contains a non-finite value -- refusing "
                "to compute a norm over it (NaN/Inf must never be treated as zero)"
            )
        # .item() forces float64 Python-float accumulation, matching
        # the C++ mirror's FP64 accumulation exactly.
        sum_of_squares += float(torch.sum(flat * flat).item())
    return math.sqrt(sum_of_squares)


@dataclass(slots=True, frozen=True)
class ClippingOutcome:
    """Local-only result of one clipping operation. ``clipping_factor``
    and ``pre_clip_norm`` exist for the caller's own local decision-
    making and MUST NEVER be transmitted, logged, or included in any
    signed attestation (Work Area G/I) -- only ``clipped_delta``
    crosses the process boundary, via the existing masked-update
    pipeline."""

    clipped_delta: dict[str, torch.Tensor]
    pre_clip_norm: float
    clipping_factor: float


def clip_delta_to_l2_norm(
    delta: dict[str, torch.Tensor], clip_norm: float, *, numerical_floor: float = 1e-12
) -> ClippingOutcome:
    """Deterministic global L2 clipping -- Work Area G.

    ``clipping_factor = min(1, clip_norm / max(norm, numerical_floor))``,
    applied uniformly to every tensor (canonical order, matching
    compute_global_l2_norm). Exact-bound and oversized updates both
    scale correctly via this formula; a zero-norm update yields
    ``clipping_factor == 1.0`` (no-op, not a division by zero, thanks
    to ``numerical_floor``). Deterministic: the same delta and clip_norm
    always produce the same clipped_delta and clipping_factor -- no
    randomness, no data-dependent branching beyond the min() itself.
    """
    if not math.isfinite(clip_norm) or clip_norm <= 0.0:
        raise UserLevelClippingError(
            f"clip_norm must be a positive finite value, got {clip_norm!r}"
        )
    pre_clip_norm = compute_global_l2_norm(delta)
    clipping_factor = min(1.0, clip_norm / max(pre_clip_norm, numerical_floor))
    clipped: dict[str, torch.Tensor] = {}
    for tensor_name in sorted(delta.keys()):
        clipped[tensor_name] = delta[tensor_name] * clipping_factor
    return ClippingOutcome(
        clipped_delta=clipped,
        pre_clip_norm=pre_clip_norm,
        clipping_factor=clipping_factor,
    )


def compute_total_element_count(delta: dict[str, torch.Tensor]) -> int:
    """Total element count across every tensor -- the ``N`` in
    ``compute_quantization_margin`` below, and the same figure
    ``encode_weighted_delta`` already computes as
    ``WeightedEncodingResult.total_elements``. A tiny standalone
    helper so callers that need it before encoding (to size the
    quantization margin ahead of building the signed task/attestation)
    don't need to run the encoder first."""
    return sum(tensor.numel() for tensor in delta.values())


def compute_quantization_margin(
    total_element_count: int, profile: FixedPointEncodingProfile
) -> float:
    """Python mirror of fl::coordinator::compute_quantization_margin
    (secure_aggregation_encoding.{hpp,cpp}) -- see
    docs/secure-user-level-dp-semantics.md section 11 for the
    derivation. Both sides must compute byte-identical values; verified
    by a cross-language fixture, not merely by code inspection."""
    if not math.isfinite(profile.scale_factor) or profile.scale_factor <= 0.0:
        return math.inf
    per_element_bound = 0.5 / profile.scale_factor
    return math.sqrt(total_element_count) * per_element_bound


def compute_effective_sensitivity(
    clip_norm: float, quantization_margin: float
) -> float:
    """``effective_sensitivity = clip_norm + quantization_margin`` --
    the value noise must be calibrated against. A named function
    (rather than inlining the addition at every call site) purely so
    C++ and Python express the same two-quantity combination
    identically everywhere it appears."""
    return clip_norm + quantization_margin
