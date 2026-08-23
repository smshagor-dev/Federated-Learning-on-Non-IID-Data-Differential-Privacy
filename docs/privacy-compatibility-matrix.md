# Privacy Compatibility Matrix

**Status: implemented & tested.** Source of truth:
`python/src/fl_platform/privacy/compatibility.py`. Hand-mirrored (not
generated) in Go: `go/internal/privacy/compatibility.go` — kept
behaviorally identical by convention and cross-checked by test, not by a
shared codegen step. Consumed by the web experiment builder via the Go
`/privacy/compatibility` endpoint.

## Classifications

| Status | Meaning |
|---|---|
| `SUPPORTED` | Implemented and covered by a real test exercising the actual mechanism, not just config validation. |
| `EXPERIMENTAL` | Implemented, usable, but with a documented open question or boundary limitation; it is not a full stated guarantee outside the documented boundary. |
| `UNSUPPORTED` | Not implemented **or not privacy-proven for the claimed mechanism**; a run requesting this combination is rejected before it starts. |
| `DEFERRED` | Explicitly out of the current research scope, not merely missing by oversight. |

`is_usable(status)` — only `SUPPORTED` and `EXPERIMENTAL` may run.
Unsupported combinations must fail before a task is assigned to a worker.

## Sample-level DP × algorithm

Sample-level DP wraps the local training loop with Opacus. Compatibility
depends on how the local optimization step differs from plain SGD.

| Algorithm | Status | Why |
|---|---|---|
| fedavg | SUPPORTED | plain local SGD; real Opacus `PrivacyEngine` wrapping tested |
| fedprox | SUPPORTED | proximal term is added to the loss before backprop; Opacus's per-sample gradient hook sees the combined loss correctly |
| fedadagrad / fedadam / fedyogi | SUPPORTED | server-side optimizer variant only; local loop is identical to fedavg |
| scaffold | UNSUPPORTED | control-variate correction composes with DP-SGD's clip-then-noise step in a way not validated to preserve the stated epsilon |
| fedsam | UNSUPPORTED | requires two forward/backward passes per batch; not validated against Opacus's per-sample gradient hooks |
| ditto | DEFERRED | trains a second personalized model per client; interaction with the global model's DP-SGD loop is unvalidated research |
| per_fedavg | DEFERRED | MAML-style inner/outer-loop meta-gradient needs second-order gradients Opacus's hooks do not support |

## User-level DP × algorithm

User-level DP protects a client's complete shared-model contribution through
central clipping and Gaussian noise. Server-only optimizer variants remain
compatible because they operate after the private aggregate release.
Algorithms that maintain additional state or expose additional model outputs
need an explicit argument that the claimed accountant covers every relevant
release/state path.

| Algorithm | Status | Why |
|---|---|---|
| fedavg / fedprox / fedadagrad / fedadam / fedyogi | SUPPORTED | central clip+noise on the shared client contribution; server optimizer applies after the private release |
| fedsam | SUPPORTED | submits a fedavg-shaped global update; aggregated identically to fedavg |
| scaffold | **UNSUPPORTED** | SCAFFOLD maintains control-variate state in addition to the model update. The current user-level accountant covers the clipped/noised model release, but there is no formal proof that the control-variate state/release path is covered by the same client-level guarantee. It therefore fails closed instead of being labelled experimental/usable. |
| ditto / per_fedavg | EXPERIMENTAL | the global update is protected identically to fedavg; the personalized/adapted model is explicitly not covered by this mechanism — the boundary must remain visible in research claims |

## Hybrid DP × algorithm

`hybrid_status(algorithm)` takes the worse of the sample-level and user-level
statuses (rank order `DEFERRED < UNSUPPORTED < EXPERIMENTAL < SUPPORTED`).
Hybrid mode requires both mechanisms to be usable.

Examples:

- `fedavg`: `SUPPORTED` + `SUPPORTED` → `SUPPORTED`
- `scaffold`: `UNSUPPORTED` + `UNSUPPORTED` → `UNSUPPORTED`
- `ditto`: `DEFERRED` + `EXPERIMENTAL` → `DEFERRED`

## Adaptive clipping

Adaptive clipping modifies user-level DP's clipping process. Its mechanism
ledger remains separate for auditability, but that does not automatically make
its privacy loss composition-independent. If the adaptive statistic and model
release are jointly claimed under the same add/remove-client adjacency, their
RDP costs must be composed before reporting an overall user/client-level
privacy guarantee. See `privacy-mathematics.md` and
`research-correctness-contract.md`.
