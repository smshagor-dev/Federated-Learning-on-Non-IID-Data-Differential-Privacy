# Shared-Backbone / Local-Head Personalization Architecture

**Status: implemented & tested.** Source:
`python/src/fl_platform/models/personalization.py`,
`python/src/fl_platform/models/factory.py`. Tests:
`ModelRegistryTests.test_lifecycle_and_resolve_for_task` (Python),
exercised end-to-end through the C++ aggregation manifest checks in
`cpp/coordinator/tests/aggregation_manifest_test.cpp` and
`tests/baseline/test_algorithm_expansion_integration.py`'s
`test_local_head_tensor_rejected_by_coordinator`.

## The design decision

Personalization (which parameters are shared/aggregated vs. which stay
local to a client) is expressed as **parameter-name prefixes against an
ordinary `nn.Module`'s `state_dict()`** — not a custom `Module` subclass,
not a wrapper type, not a second model object. Any architecture works as
long as its shared and personalized parameters are cleanly
prefix-separated:

| Architecture | Shared prefix | Personalized prefix |
|---|---|---|
| `groupnorm_cnn` (real `GroupNormCNN`) | `"features."` | `"classifier."` |
| `personalizable_bridge` (small bridge model, below) | `"backbone"` | `"head"` |

This was chosen over subclassing because every existing training
algorithm (FedAvg/FedProx/SCAFFOLD from the Foundation, Aggregation Core, and Coordinator Runtime phases, plus FedSAM this
phase) already operates on a plain `nn.Module` via
`named_parameters()`/`state_dict()` — a custom personalization-aware
`Module` type would either have to be threaded through every one of
those call sites, or silently not work with them. Prefix-based selection
needs no changes to any existing training code.

## `PersonalizableBridgeModel`

A small, *real* two-layer linear network (not placeholder weights) sized
to fit the CLI bridge's small explicit tensor manifests (see
[aggregation-manifests.md](aggregation-manifests.md)) — one flat
`backbone` parameter tensor (shared) and one flat `head` parameter tensor
(personalized), used throughout the Algorithm Expansion phase test suite so the
coordinator's real transport path can be exercised end-to-end without
needing the full `GroupNormCNN`.

## Utilities (`models/personalization.py`)

* `parameter_names_with_prefix(model, prefixes)` — the name-selection
  primitive everything else builds on.
* `shared_state_dict(model, shared_prefixes)` /
  `personalized_state_dict(model, personalized_prefixes)` — extract just
  the shared or just the personalized tensors as a plain dict, for
  submission/storage.
* `apply_partial_state(model, partial_state)` — the inverse: load only
  the given tensors into a model, leaving every other parameter
  untouched. Raises on an unknown parameter name rather than silently
  ignoring a typo.
* `compute_schema_hash(model)` — a SHA-256 fingerprint of `(name, shape,
  dtype)` tuples, sorted by name, truncated to 16 hex chars. Used to
  detect a personalized checkpoint being loaded against an incompatible
  architecture (see [personalized-model-store.md](personalized-model-store.md))
  and to gate model registry `DRAFT → VALIDATED` transitions (see
  [model-registry.md](model-registry.md)). Deliberately *not* Python's
  `hash()` (salted per-process, not stable across runs) and not a raw
  tensor-value checksum (would change every training step) — this only
  fingerprints shape/dtype, which changes only when the architecture
  itself changes.
* `describe_model(...)` — bundles all of the above into a
  `ModelMetadata` record ready to register with the model registry.

## What is deliberately not implemented

A GroupNorm-substituted ResNet-18 was not added this phase: it is
enough new surface (residual block restructuring, weight init,
downsample-path GroupNorm placement) that getting it right without a
real training run to validate against was judged not safe to add under
this phase's scope. MLP and ViT architectures are likewise not
added — see [known-limitations.md](known-limitations.md).
