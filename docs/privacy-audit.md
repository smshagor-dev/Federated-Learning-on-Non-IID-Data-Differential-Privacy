# Privacy Audit

## Verified Privacy Model

The current implementation supports a client-level privacy mechanism only.

## What Is Actually Implemented

- Per-batch gradient clipping during local training for optimization stability.
- Post-training client update clipping to `max_grad_norm`.
- Gaussian noise addition to the clipped client update using `noise_multiplier * max_grad_norm`.
- A subsampled Gaussian RDP accountant based on client sampling rate.

## What Is Not Implemented

- Sample-level DP
- Opacus
- PRV accountant
- Per-layer clipping
- Secure RNG mode
- Secure aggregation
- Cryptographic masking
- Privacy ledger persistence
- Separate accounting for multiple privacy definitions
- Personalized privacy accounting
- Adaptive clipping

## Source of Truth

- Update clipping and noise:
  - `federated/client.py`
- RDP accountant:
  - `federated/dp_accountant.py`
- DP configuration:
  - `config.yaml`

## Privacy Data Flow

1. A client trains locally using its private shard.
2. If DP is enabled, per-batch gradients are clipped.
3. The local model update `delta = w_local - w_global` is formed.
4. The update is clipped to the configured L2 norm bound.
5. Gaussian noise is added to the clipped update.
6. The noised update is returned to the in-process server.
7. The accountant advances one step per communication round.

## Privacy Assumptions Observed

- Record unit for accounting is one client, not one sample.
- Client sampling is modeled as uniform with fixed sampling rate.
- Each round is treated as one subsampled Gaussian mechanism step.
- Gaussian noise calibration is coupled to the clipping bound.

## Exposure Points

- Raw training samples remain visible to the process that hosts local training.
- Pre-noise local model state is returned for diagnostics and held in memory.
- Client deltas exist in memory before clipping and before noising.
- No persistence encryption or at-rest controls are implemented.
- No network transport security is relevant because execution is single-process.

## Current Privacy Risks

- The system may be described incorrectly if users assume sample-level DP.
- The same epsilon display may be over-interpreted as a general privacy guarantee.
- Diagnostic access to local states increases insider exposure risk.
- SCAFFOLD control variate updates are based on already-noised deltas, but there is no formal proof bundled with the codebase.
- There is no privacy budget enforcement gate that stops training when a target budget is exceeded.

## Milestone 1 Outcome

Milestone 1 preserves this behavior exactly, documents it, and avoids overstating guarantees. No new privacy claim is introduced.
