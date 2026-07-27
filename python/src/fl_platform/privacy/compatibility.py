"""Privacy compatibility matrix: which (algorithm, privacy mechanism)
combinations are actually implemented and validated, vs. experimental,
vs. not supported at all. This is the single source of truth the
Python worker, the C++ coordinator (via the Go API's mirrored table —
see docs/privacy-compatibility-matrix.md), and the web experiment
builder all defer to. Unsupported combinations must fail before a task
is ever assigned to a worker — see docs/sample-level-dp.md's "Never
silently execute non-private training when private training was
requested" requirement.

Classification meanings:
  SUPPORTED    — implemented and covered by a real test that exercises
                 the actual mechanism (not just config validation).
  EXPERIMENTAL — implemented, but with a documented open question about
                 correctness or a gap in test coverage; usable, not a
                 stated guarantee.
  UNSUPPORTED  — not implemented; a run requesting this combination is
                 rejected before it starts.
  DEFERRED     — explicitly out of scope for this phase (see
                 docs/known-limitations.md), not merely unimplemented by
                 oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

ALGORITHMS = (
    "fedavg",
    "fedprox",
    "scaffold",
    "fedadagrad",
    "fedadam",
    "fedyogi",
    "fedsam",
    "ditto",
    "per_fedavg",
)


class CompatibilityStatus(StrEnum):
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"
    DEFERRED = "deferred"


@dataclass(slots=True, frozen=True)
class CompatibilityEntry:
    status: CompatibilityStatus
    reason: str


# Sample-level DP wraps the *local training loop* with Opacus. Whether a
# given algorithm is compatible depends entirely on how that algorithm's
# local step differs from plain SGD:
#
# - fedavg/fedprox/fedadagrad/fedadam/fedyogi: the server-side optimizer
#   variants (adagrad/adam/yogi) don't change the *client's* local loop
#   at all (see fl_core::AggregationAlgorithm's comment: they aggregate
#   differently server-side, not train differently client-side) — so
#   they inherit fedavg's local-loop compatibility directly. fedprox
#   only adds a proximal term to the loss before backprop, which Opacus's
#   per-sample gradient hook computes correctly.
# - scaffold: applies a control-variate correction to the gradient
#   *after* the per-sample-clipped, noised gradient Opacus produces —
#   composing that correction with DP-SGD's clip-then-noise step in a
#   way that preserves the stated privacy guarantee is a genuine open
#   question this project has not validated. Rejected, not silently
#   approximated.
# - fedsam: requires two forward/backward passes per batch (one to find
#   the sharpness-aware perturbation, one for the actual gradient) —
#   Opacus's per-sample gradient hooks are not validated against that
#   pattern.
# - ditto/per_fedavg: train a second, personalized model per client in
#   addition to (ditto) or via meta-adaptation of (per_fedavg) the
#   global model. Per-FedAvg's inner/outer-loop meta-gradient in
#   particular needs second-order gradients Opacus's hooks don't
#   support. Both are DEFERRED, not merely UNSUPPORTED, since making
#   them work is real, non-trivial research, not an integration gap.
SAMPLE_LEVEL_DP_COMPATIBILITY: dict[str, CompatibilityEntry] = {
    "fedavg": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "plain local SGD; real Opacus PrivacyEngine wrapping tested",
    ),
    "fedprox": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "proximal term is added to the loss before backprop; Opacus's per-sample "
        "gradient hook sees the combined loss correctly",
    ),
    "fedadagrad": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server-side optimizer variant only; local loop is identical to fedavg",
    ),
    "fedadam": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server-side optimizer variant only; local loop is identical to fedavg",
    ),
    "fedyogi": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server-side optimizer variant only; local loop is identical to fedavg",
    ),
    "scaffold": CompatibilityEntry(
        CompatibilityStatus.UNSUPPORTED,
        "control-variate correction composes with DP-SGD's clip-then-noise step in a "
        "way not validated to preserve the stated epsilon; rejected rather than "
        "silently approximated",
    ),
    "fedsam": CompatibilityEntry(
        CompatibilityStatus.UNSUPPORTED,
        "requires two forward/backward passes per batch (sharpness-aware "
        "perturbation); not validated against Opacus's per-sample gradient hooks",
    ),
    "ditto": CompatibilityEntry(
        CompatibilityStatus.DEFERRED,
        "trains a second personalized model per client; interaction between that "
        "second model's training and the global model's DP-SGD loop is unvalidated "
        "research, not an integration gap",
    ),
    "per_fedavg": CompatibilityEntry(
        CompatibilityStatus.DEFERRED,
        "MAML-style inner/outer-loop meta-gradient requires second-order gradients; "
        "Opacus's per-sample hooks do not support this",
    ),
}

# User-level DP clips and noises the *aggregate* client delta centrally
# in C++, after local training completes — it is largely algorithm-
# agnostic (see docs/user-level-dp.md), but two algorithm-specific
# clarifications are needed before it can be called SUPPORTED for an
# algorithm rather than merely EXPERIMENTAL:
#   - scaffold submits a control-variate delta alongside the model
#     delta; only the model delta is clipped/noised (control variates
#     are coordination state, not privacy-sensitive model updates in the
#     same sense) — implemented that way, but not covered by a
#     dedicated test proving the control-variate channel is genuinely
#     excluded from the clip/noise path.
#   - ditto/per_fedavg produce a personalized model in addition to the
#     global update; the global update is protected the same as fedavg's,
#     but the personalized model is explicitly NOT protected by this
#     mechanism (see docs/hybrid-dp.md) — EXPERIMENTAL until that
#     boundary has its own test coverage, not because the global-update
#     path itself is in doubt.
USER_LEVEL_DP_COMPATIBILITY: dict[str, CompatibilityEntry] = {
    "fedavg": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "central clip+noise on the aggregate delta; tested",
    ),
    "fedprox": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server-side aggregation is identical to fedavg once the client submits "
        "its delta",
    ),
    "fedadagrad": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server optimizer applies after clip+noise, unaffected",
    ),
    "fedadam": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server optimizer applies after clip+noise, unaffected",
    ),
    "fedyogi": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "server optimizer applies after clip+noise, unaffected",
    ),
    "scaffold": CompatibilityEntry(
        CompatibilityStatus.EXPERIMENTAL,
        "control-variate delta is excluded from clip/noise by construction but that "
        "exclusion has no dedicated test yet",
    ),
    "fedsam": CompatibilityEntry(
        CompatibilityStatus.SUPPORTED,
        "fedsam submits a fedavg-shaped global update (see "
        "fl_core::AggregationAlgorithm); the coordinator aggregates it "
        "identically to fedavg",
    ),
    "ditto": CompatibilityEntry(
        CompatibilityStatus.EXPERIMENTAL,
        "global update is protected identically to fedavg; the personalized model is "
        "explicitly not covered — boundary untested",
    ),
    "per_fedavg": CompatibilityEntry(
        CompatibilityStatus.EXPERIMENTAL,
        "global update is protected identically to fedavg; the personalized/adapted "
        "model is explicitly not covered — boundary untested",
    ),
}


def sample_level_status(algorithm: str) -> CompatibilityEntry:
    if algorithm not in SAMPLE_LEVEL_DP_COMPATIBILITY:
        return CompatibilityEntry(
            CompatibilityStatus.UNSUPPORTED, f"unknown algorithm '{algorithm}'"
        )
    return SAMPLE_LEVEL_DP_COMPATIBILITY[algorithm]


def user_level_status(algorithm: str) -> CompatibilityEntry:
    if algorithm not in USER_LEVEL_DP_COMPATIBILITY:
        return CompatibilityEntry(
            CompatibilityStatus.UNSUPPORTED, f"unknown algorithm '{algorithm}'"
        )
    return USER_LEVEL_DP_COMPATIBILITY[algorithm]


def hybrid_status(algorithm: str) -> CompatibilityEntry:
    """Hybrid DP requires *both* mechanisms to be at least usable
    (EXPERIMENTAL or SUPPORTED) — the worse of the two statuses wins,
    since hybrid mode composes both, not just one."""
    sample = sample_level_status(algorithm)
    user = user_level_status(algorithm)
    rank = {
        CompatibilityStatus.DEFERRED: 0,
        CompatibilityStatus.UNSUPPORTED: 1,
        CompatibilityStatus.EXPERIMENTAL: 2,
        CompatibilityStatus.SUPPORTED: 3,
    }
    worse = sample if rank[sample.status] <= rank[user.status] else user
    if worse.status in (CompatibilityStatus.UNSUPPORTED, CompatibilityStatus.DEFERRED):
        return CompatibilityEntry(
            worse.status,
            f"hybrid DP requires both mechanisms to be usable; "
            f"{'sample-level' if worse is sample else 'user-level'} DP is "
            f"{worse.status} for '{algorithm}': {worse.reason}",
        )
    return CompatibilityEntry(
        worse.status,
        f"hybrid DP for '{algorithm}': sample-level={sample.status}, "
        f"user-level={user.status}",
    )


def is_usable(status: CompatibilityStatus) -> bool:
    """SUPPORTED or EXPERIMENTAL may run; UNSUPPORTED/DEFERRED must be
    rejected before a run starts — see the module docstring's "must fail
    before a task is ever assigned" requirement."""
    return status in (CompatibilityStatus.SUPPORTED, CompatibilityStatus.EXPERIMENTAL)
