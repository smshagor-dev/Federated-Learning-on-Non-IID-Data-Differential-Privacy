# Privacy Compatibility Matrix

**Status: implemented & tested.** Source of truth:
`python/src/fl_platform/privacy/compatibility.py`. Hand-mirrored (not
generated) in Go: `go/internal/privacy/compatibility.go` — kept
behaviorally identical by convention and cross-checked by test, not by a
shared codegen step (see known-limitations.md's Privacy Engineering
Phase section for why this is a documented limitation rather than a
solved problem). Consumed by the web experiment builder via the Go
`/privacy/compatibility` endpoint.

## Classifications

| Status | Meaning |
|---|---|
| `SUPPORTED` | Implemented and covered by a real test exercising the actual mechanism, not just config validation. |
| `EXPERIMENTAL` | Implemented, usable, but with a documented open question about correctness or a gap in test coverage — not a stated guarantee. |
| `UNSUPPORTED` | Not implemented; a run requesting this combination is rejected before it starts. |
| `DEFERRED` | Explicitly out of scope for this phase (see known-limitations.md), not merely unimplemented by oversight. |

`is_usable(status)` — only `SUPPORTED` and `EXPERIMENTAL` may run.
Unsupported combinations must fail **before a task is ever assigned to a
worker**, not partway through a round; this is enforced at `CreateRun`
validation time, not discovered lazily.

## Sample-level DP × algorithm

Sample-level DP wraps the local training loop with Opacus. Compatibility
depends entirely on how an algorithm's local step differs from plain SGD
— the server-side aggregation variant (adagrad/adam/yogi) never matters
here, since none of them change what happens on the client.

| Algorithm | Status | Why |
|---|---|---|
| fedavg | SUPPORTED | plain local SGD; real Opacus `PrivacyEngine` wrapping tested |
| fedprox | SUPPORTED | proximal term is added to the loss before backprop; Opacus's per-sample gradient hook sees the combined loss correctly |
| fedadagrad / fedadam / fedyogi | SUPPORTED | server-side optimizer variant only; local loop is identical to fedavg |
| scaffold | UNSUPPORTED | control-variate correction composes with DP-SGD's clip-then-noise step in a way not validated to preserve the stated epsilon; rejected rather than silently approximated |
| fedsam | UNSUPPORTED | requires two forward/backward passes per batch (sharpness-aware perturbation); not validated against Opacus's per-sample gradient hooks |
| ditto | DEFERRED | trains a second personalized model per client; interaction with the global model's DP-SGD loop is unvalidated research |
| per_fedavg | DEFERRED | MAML-style inner/outer-loop meta-gradient needs second-order gradients Opacus's hooks don't support |

## User-level DP × algorithm

User-level DP clips and noises the aggregate client delta centrally in
C++, after local training completes — largely algorithm-agnostic (see
[user-level-dp.md](user-level-dp.md)), but two cases stay `EXPERIMENTAL`
pending dedicated boundary tests rather than because the core path is in
doubt.

| Algorithm | Status | Why |
|---|---|---|
| fedavg / fedprox / fedadagrad / fedadam / fedyogi | SUPPORTED | central clip+noise on the aggregate delta; server optimizer applies after, unaffected |
| fedsam | SUPPORTED | submits a fedavg-shaped global update; aggregated identically to fedavg |
| scaffold | EXPERIMENTAL | control-variate delta is excluded from clip/noise by construction, but that exclusion has no dedicated test yet |
| ditto / per_fedavg | EXPERIMENTAL | the global update is protected identically to fedavg; the personalized/adapted model is explicitly **not** covered by this mechanism (see [hybrid-dp.md](hybrid-dp.md)) — boundary untested |

## Hybrid DP × algorithm

`hybrid_status(algorithm)` takes the **worse** of the sample-level and
user-level statuses (rank order `DEFERRED < UNSUPPORTED < EXPERIMENTAL <
SUPPORTED`) — hybrid mode composes both mechanisms, so it is only as
usable as its weaker half. E.g. `scaffold` is `UNSUPPORTED` for hybrid
(sample-level is `UNSUPPORTED`, which dominates user-level's
`EXPERIMENTAL`); `ditto` is `DEFERRED` for hybrid (sample-level is
`DEFERRED`, which dominates user-level's `EXPERIMENTAL`).

## Adaptive clipping

Adaptive clipping is a modifier on user-level DP's clip step (see
[adaptive-clipping.md](adaptive-clipping.md)), not a fourth entry in this
matrix — it inherits user-level DP's per-algorithm compatibility
directly rather than having its own table.
