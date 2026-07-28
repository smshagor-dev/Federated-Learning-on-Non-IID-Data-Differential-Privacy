# Secure Adaptive Clipping Runtime - Completion Report

See [secure-adaptive-clipping-runtime-audit.md](secure-adaptive-clipping-runtime-audit.md)
for the pre-implementation audit and
[secure-adaptive-clipping-semantics.md](secure-adaptive-clipping-semantics.md)
for the full mechanism specification. `SECAGG_NO_DROPOUT_EXPERIMENTAL`
remains the provider throughout. The trust statement does not change:
this is implemented, locally tested, and now runtime validated for the
no-dropout honest-client-dependent path, but it is still experimental,
not malicious-client secure, and not a production privacy claim.

## What is now real

The secure adaptive-clipping path is no longer just a design and unit
test surface. It now has:

- worker-side private binary indicator creation and pairwise masking,
- signed adaptive-clipping task bindings carried alongside masked
  updates,
- coordinator-side binding verification, complete-cohort indicator
  reconstruction, and one-step clip-state advancement,
- adaptive-clipping ledger commits and privacy metrics/projection
  publication for the same real run,
- a dedicated live Docker validation harness:
  `scripts/validate_secure_adaptive_clipping.py`,
- a dedicated compose override for that harness:
  `infra/compose/docker-compose.secure-adaptive-clipping.yml`.

## Validation completed on July 28, 2026

Fresh evidence used for closure in this pass:

- `python scripts/check_project_terminology.py` - passed.
- `python -m pytest python/tests/test_adaptive_clipping_binding.py python/tests/test_coordinator_task_verifier.py` - 30 passed.
- `cmake -S cpp -B build/cpp-grpc-adaptive-fresh -DCMAKE_BUILD_TYPE=Debug` on Windows - fresh configure succeeded and correctly skipped gRPC targets locally because native gRPC discovery is unavailable on this host.
- `docker compose -f infra/compose/docker-compose.dev.yml -f infra/compose/docker-compose.security.yml -f infra/compose/docker-compose.secure-cohort-handshake.yml -f infra/compose/docker-compose.secure-adaptive-clipping.yml build coordinator api python-worker web` - passed.
- The coordinator image build performed a fresh real gRPC compile of `fl_coordinator_grpc_server` inside Docker; this is the runtime-valid build evidence, not the stale historical `/app/build/cpp-grpc` cache.
- `python scripts/validate_secure_adaptive_clipping.py` - **46/46 checks passed**.

## Live runtime evidence

The fresh adaptive-clipping Docker run on July 28, 2026 proved all of
the following in one end-to-end pass:

- all three workers reached `READY_FOR_MASKED_TRAINING`,
- all three workers applied real worker-side clipping and submitted a
  masked adaptive-clipping update the coordinator accepted,
- the security-event journal contained real
  `SECURE_ADAPTIVE_CLIPPING_CONFIGURATION_ACCEPTED`,
  `SECURE_ADAPTIVE_CLIPPING_INDICATOR_ACCEPTED`,
  `SECURE_ADAPTIVE_CLIPPING_COMPLETE_COHORT_RECONSTRUCTED`,
  `SECURE_ADAPTIVE_CLIPPING_NEXT_STATE_PUBLISHED`, and
  `SECURE_ADAPTIVE_CLIPPING_ROUND_COMPLETED` events,
- the run reached `COMPLETED`,
- the model version advanced from `v0` to `v1`,
- no worker fell back to the cleartext `ClientResult` path,
- the coordinator structured log contained
  `AGGREGATION_COMPLETED`, `MODEL_VERSION_UPDATED`,
  `CHECKPOINT_COMPLETED`, and `RUN_COMPLETED`,
- `GET /api/v1/coordinator/runs/{run_id}/privacy/metrics` reported
  `has_clipping=true` with a positive `current_clip_value`,
- `GET /api/v1/coordinator/runs/{run_id}/privacy/ledger` reported
  exactly one adaptive-clipping ledger entry for round 1,
- `GET /api/v1/coordinator/runs/{run_id}/privacy/projection` reported
  `has_clipping=true`,
- the existing secure user-level privacy health/budget/status routes
  still reported the same real run consistently.

Observed live from the passing run:

- adaptive-clipping `current_clip_value`: `0.0125`
- secure user-level `epsilon_spent`: `5.303`

## One harness issue found and fixed during validation

The first live run completed the mechanism successfully but one harness
assertion failed because it assumed `GET /api/v1/coordinator/runs/{id}`
echoed nested `privacy.adaptive_clipping` config. That route does not
currently expose the nested privacy object. The harness was corrected
to assert against the routes that actually publish adaptive-clipping
state in this codebase:

- `/api/v1/coordinator/runs/{run_id}/privacy/metrics`
- `/api/v1/coordinator/runs/{run_id}/privacy/ledger`
- `/api/v1/coordinator/runs/{run_id}/privacy/projection`

The mechanism itself did not fail; the corrected harness was rerun and
passed cleanly.

## Current status

Secure adaptive clipping under secure aggregation is now:

- Implemented
- Locally tested
- Runtime validated for the no-dropout Docker path
- Experimental
- Honest-client dependent
- No-dropout only

Still not claimed here:

- dropout tolerance,
- malicious-client security,
- cryptographic proof that a worker's indicator truthfully reflects its
  unclipped norm,
- production readiness,
- any privacy guarantee stronger than the documented trust model in the
  semantics report.
