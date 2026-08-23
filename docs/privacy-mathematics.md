# Privacy Mathematics: the Critical Privacy Rule

**Status: research-correctness revision.** This document defines how privacy
values may be interpreted and composed. The key distinction is between
**mechanism separation for auditability** and **privacy composition for a
joint release**.

## The rule

**Never collapse mechanisms protecting different neighboring relations into
one epsilon. When multiple released mechanisms protect the same neighboring
relation, their privacy loss must be composed before claiming one overall
guarantee for the joint release.**

The repository contains three privacy mechanism families:

| Mechanism | Protected change / adjacency | Where it is computed | Domain type |
|---|---|---|---|
| Sample-level DP | One training example within one client's local dataset | Python worker, via Opacus (`fl_platform.privacy.accounting.SampleLevelAccountant`) | `SampleLevelLedgerEntry` |
| User/client-level DP | One client's complete round contribution | C++ coordinator, centrally (`fl::core::UserLevelAccountant`) | `UserLevelLedgerEntry` |
| Adaptive clipping statistic | One client's contribution to the clipping statistic when interpreted at client adjacency | Coordinator-side adaptive clipping accountant | `AdaptiveClippingLedgerEntry` |

Sample-level and client-level epsilons answer different privacy questions and
must remain separately reported. They are not addable substitutes for one
another.

Adaptive clipping requires a more careful statement than the older
"different epsilon, never compose" rule. Its noised count is a second release
whose sensitivity is driven by client participation. If the publication claim
uses the same add/remove-client neighboring relation for that statistic and
the model release, then the two client-level mechanisms are part of one joint
release and must be composed (preferably in RDP before conversion to
`(epsilon, delta)`). Keeping two ledger rows is still useful, but separate
ledgers do not remove the composition requirement.

`federated/privacy_research.py::compose_same_adjacency_rdp` implements this
research-facing rule explicitly: all mechanisms must declare the same
adjacency, otherwise composition is rejected.

## What remains separate in implementation

* **Separate accountant instances and state.** Sample-level, user-level and
  adaptive-clipping mechanism state must never share mutable accountant
  instances. This prevents accidental cross-mechanism state corruption and
  preserves traceability.
* **Separate ledger tables.** `PrivacyLedger` and the C++ run state expose
  separate sample-level, user-level and adaptive-clipping records. This is an
  auditability property, not a proof that the releases are composition-free.
* **Separate budget policies where the neighboring relation differs.** A
  sample-level budget cannot be substituted for a user/client-level budget.
* **Same-adjacency publication composition.** If two mechanisms are jointly
  released under the same client-level adjacency, an overall client-level
  claim must include both privacy costs.

The existing `PrivacyMetricsSnapshot.sample_epsilon` remains valid as a
worst-case reduction *within* the sample-level mechanism. It is not a combined
sample+user privacy guarantee.

## RDP accounting (Mironov 2017/2019, subsampled Gaussian mechanism)

The root `MomentsAccountant`, the coordinator user-level accountant and
Opacus's RDP implementation use the same integer-order subsampled-Gaussian
formula for the shared orders:

```text
q = sample_rate, sigma = noise_multiplier
RDP(alpha) = (1/(alpha-1)) * log(
  sum_{k=0}^{alpha}
    C(alpha,k) * (1-q)^(alpha-k) * q^k * exp(k(k-1)/(2*sigma^2))
)
```

For `q=1`, this reduces to the ordinary Gaussian-mechanism RDP:

```text
RDP(alpha) = alpha / (2*sigma^2)
```

Composition over released mechanisms/steps that share the same adjacency is
additive in RDP:

```text
RDP_total(alpha) = sum_j RDP_j(alpha)
```

Conversion to `(epsilon, delta)` is then:

```text
epsilon = min_alpha [ RDP_total(alpha) + log(1/delta)/(alpha-1) ]
```

This is preferable to adding independently converted epsilons because the RDP
composition retains the order-wise information until the final conversion.

## Adaptive clipping sampling caveat

The current Python/C++ adaptive-clipping accountant has historically treated
the privatized cohort count as an ordinary Gaussian mechanism with
`sample_rate=1.0` **conditional on the selected cohort**. That mechanism-level
ledger is useful for tracking the released statistic, but it is not by itself
a proof of the tight population-level add/remove-client guarantee when client
selection is Poisson-subsampled before the count is formed.

Therefore:

1. do not publish a single end-to-end client-level epsilon for
   user-level model release + adaptive clipping merely by reading the two
   existing ledger values;
2. first state the population adjacency and client-sampling model explicitly;
3. use a composition/accounting path whose `sample_rate` matches that stated
   model for each release;
4. compose the same-adjacency RDP curves before converting to epsilon.

The research utility in `federated/privacy_research.py` is designed for this
explicit workflow. The distributed runtime's adaptive-clipping accountant
must be upgraded to the same population-level sampling semantics before the
platform claims a composed end-to-end epsilon for that mode.

## DP + SCAFFOLD boundary

The active root SCAFFOLD implementation maintains control-variate state in
addition to the noised global-model release. The existing root client-level
accountant only models the clipped/noised model-update mechanism; it does not
provide a proof for additional control-variate state or releases.

For that reason the root runtime now fails closed for DP-enabled SCAFFOLD.
SCAFFOLD remains available without DP as an optimization baseline. A future
DP-SCAFFOLD mode must specify the complete release/state semantics and prove
that its accountant covers them before the fail-closed guard is removed.

## Validation

Before relying on the legacy-derived `federated.dp_accountant.MomentsAccountant`,
its per-order RDP values were compared directly with Opacus at shared integer
orders (`python/tests/test_privacy_accounting.py`). The C++ implementation is
also covered by golden parity tests against the same reference model.

The research-correctness tests additionally verify that:

- target-epsilon calibration returns a privacy-safe noise multiplier;
- stricter epsilon targets require at least as much noise;
- same-adjacency RDP composition never produces a privacy cost smaller than
  either component;
- mixed-adjacency composition is rejected;
- DP-enabled SCAFFOLD is rejected in the active root runtime;
- the default root configuration stays pinned to its declared target epsilon.

## Trust model

Client-level central DP assumes the coordinator correctly clips contributions,
samples privacy noise and follows the documented sampling/accounting model.
Secure aggregation changes what the coordinator can observe; it does not by
itself prove honest client clipping or correct DP execution.

See `privacy-engineering-security-audit.md`, `secure-aggregation-threat-model.md`
and `RUNTIME.md` before making deployment or publication claims.
