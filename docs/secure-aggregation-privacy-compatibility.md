# Secure Aggregation × Privacy Mode Compatibility Matrix

Work Area T of the Secure User-Level Differential Privacy Runtime
slice. Complements `docs/privacy-compatibility-matrix.md` (which
covers algorithm × privacy-mode compatibility, unrelated to secure
aggregation) — this document covers privacy mode × secure aggregation
(`SECAGG_NO_DROPOUT_EXPERIMENTAL`) compatibility specifically, decided
once per session at `AcquireTask`'s session-creation time in
`coordinator_service.cpp`, never re-derived per participant.

| Privacy mode | Secure aggregation compatible? | Condition |
|---|---|---|
| `NONE` | Yes | Algorithm must be `fedavg` |
| `SAMPLE_LEVEL_DP` | Yes | Algorithm must be `fedavg`; sample-level DP training happens entirely worker-side (Opacus), independent of secure aggregation — unchanged from the prior slice |
| `USER_LEVEL_DP` | **Yes, as of this slice** | Algorithm must be `fedavg`; weighting must be `uniform` (fixed weight 1 per user); adaptive clipping must be disabled; the run's `user_level_privacy` config (`initial_clipping_bound`, `noise_multiplier`, `target_delta`) must all be finite and positive (`target_delta` additionally `< 1`); the effective sensitivity (`clip_norm + quantization_margin`) must stay strictly below the fixed-point profile's `max_input_magnitude`; the projected epsilon after one more accountant step must stay below `epsilon_budget` (when a budget is configured) |
| `HYBRID_DP` | **Yes, as of the Secure Hybrid Differential Privacy Runtime slice** | Every `USER_LEVEL_DP` condition (weighting/clip-norm/noise/target-delta/quantization-margin/budget) applies to the user-level layer, **plus** the run's `sample_level_privacy` config (`noise_multiplier`, `max_grad_norm`, `target_delta`) must all be finite and positive (`target_delta` additionally `< 1`); sample-level and user-level budgets/accountants are validated and reported completely separately — see `docs/secure-hybrid-dp-semantics.md` |

Cross-cutting rejections (apply regardless of privacy mode):

| Condition | Rejection reason |
|---|---|
| Algorithm is not `fedavg` | `SECURE_AGGREGATION_ALGORITHM_UNSUPPORTED` |
| Adaptive clipping enabled | `SECURE_AGGREGATION_ADAPTIVE_CLIPPING_UNSUPPORTED` — clipping indicators are not themselves securely aggregated yet |

`USER_LEVEL_DP`-specific rejections (checked in this order once the
above pass):

| Condition | Rejection reason |
|---|---|
| Weighting is not `uniform` | `SECURE_USER_LEVEL_DP_VARIABLE_WEIGHT_UNSUPPORTED` |
| `initial_clipping_bound`/`noise_multiplier`/`target_delta` invalid (non-finite, non-positive, or `target_delta >= 1`) | `SECURE_USER_LEVEL_DP_INVALID_CONFIGURATION` |
| `effective_sensitivity >= max_input_magnitude` | `SECURE_USER_LEVEL_DP_UNSAFE_QUANTIZATION_MARGIN` |
| Projected epsilon would meet/exceed `epsilon_budget` | `SECURE_USER_LEVEL_DP_BUDGET_EXHAUSTED` |

`HYBRID_DP` reuses the identical validation ladder above with
`SECURE_HYBRID_DP_*`-prefixed reason codes instead
(`SECURE_HYBRID_DP_VARIABLE_WEIGHT_UNSUPPORTED`,
`SECURE_HYBRID_DP_INVALID_CONFIGURATION`,
`SECURE_HYBRID_DP_UNSAFE_QUANTIZATION_MARGIN`,
`SECURE_HYBRID_DP_BUDGET_EXHAUSTED`), plus one check that runs first,
before the shared ladder:

| Condition | Rejection reason |
|---|---|
| `sample_level_privacy.noise_multiplier`/`max_grad_norm`/`target_delta` invalid (non-finite, non-positive, or `target_delta >= 1`) | `SECURE_HYBRID_DP_INVALID_SAMPLE_CONFIGURATION` |

At `SubmitMaskedClientUpdate` time, a `HYBRID_DP` submission
additionally requires a signed `SignedSamplePrivacyRecord` (envelope +
payload) alongside the existing signed `SignedUserLevelPrivacyAttestation`
-- verified via the identical signature/structural-binding/replay/
monotonicity/budget-decision-contradiction sequence the cleartext
`SubmitClientResult` path already uses for the same record type, closing
a pre-existing gap where `MaskedClientUpdate.sample_privacy_record_hash`
was wire-present but never populated or verified (see
`docs/secure-hybrid-dp-runtime-audit.md`). New rejection reasons:
`SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_MISSING`,
`_SAMPLE_RECORD_INVALID_SIGNATURE`, `_SAMPLE_RECORD_BINDING_MISMATCH`.

An incompatible session is created and then **immediately aborted**
(`SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE`) — it is
never left dangling in `COHORT_FORMING`. The round then proceeds via
ordinary unmasked training instead, observably (a
`kSecureAggregationSessionAborted` security event), never silently.
This is the coordinator's own decision, not a worker bypassing
masking — see `docs/secure-user-level-dp-semantics.md`'s trust
statement for why that distinction matters.

## Explicitly not reconsidered by this slice

Every restriction the prior slice (Masked Update Runtime and
No-Dropout Secure FedAvg Finalization) already established remains
unchanged: no dropout recovery, no partial-cohort finalization, no
threshold secret sharing, no malicious-client clipping verification.
See that slice's own report and `docs/secure-user-level-dp-semantics.md`'s
Mandatory Privacy Trust Statement.
