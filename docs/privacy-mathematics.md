# Privacy Mathematics: the Critical Privacy Rule

**Status: implemented & tested.** This document is the foundational
reference every other Privacy Engineering doc and dozens of code
comments point back to. Source of the rule's enforcement: there is no
single function that could violate it, because no code path in this
repository ever adds, averages, or otherwise combines two different
mechanisms' epsilon values — that absence is itself the guarantee, and
it is exercised by dedicated tests in all three languages (see each
mechanism's own test suite, cross-referenced below).

## The rule

**Never display, persist, calculate, or report one combined epsilon for
mechanisms protecting different neighboring relations.** This system
implements three independent differential-privacy mechanisms, each of
which answers a different question about what "changing one thing" means:

| Mechanism | Neighboring relation | Where it's computed | Domain type |
|---|---|---|---|
| Sample-level DP | One training example, within one client's local dataset | Python worker, via Opacus (`fl_platform.privacy.accounting.SampleLevelAccountant`) | `SampleLevelLedgerEntry` |
| User-level DP | One client's complete round contribution (the whole local update, treated as one unit) | C++ coordinator, centrally (`fl::core::UserLevelAccountant`) | `UserLevelLedgerEntry` |
| Adaptive clipping | The clip-bound statistic itself (a count query over the cohort) | C++ coordinator, centrally (`fl::core::UserLevelAccountant`, reused as a plain Gaussian mechanism accountant) | `AdaptiveClippingLedgerEntry` |

Because the neighboring relation differs, **epsilon values from
different mechanisms are not comparable, not addable, and not
substitutable for one another** — even when (as with adaptive clipping)
two mechanisms happen to reuse the identical RDP-accounting *formula*.
Reusing a formula is not reusing an epsilon; see
`fl_core/privacy.hpp`'s `AdaptiveClipController` doc comment for the
explicit statement of this distinction at the one place in the codebase
it could be most easily conflated.

## How this is enforced in practice, not just stated

* **Separate accountant types, separate state.** `SampleLevelAccountant`,
  `UserLevelAccountant`, and the adaptive-clipping accountant (which
  wraps `UserLevelAccountant` with `sample_rate=1.0` rather than sharing
  an instance) are three distinct objects with no shared mutable state,
  in both Python (`fl_platform/privacy/accounting.py`) and C++
  (`fl_core/privacy.hpp`/`.cpp`). `AccountantSeparationTests`
  (`python/tests/test_privacy_accounting.py`) asserts this at the type
  level.
* **Separate ledger tables, never joined.** `PrivacyLedger` (Go:
  `coordinator.PrivacyLedger`, C++: `RunInstance::sample_level_ledger()`/
  `user_level_ledger()`/`adaptive_clipping_ledger()`) exposes three
  independent lists. No code path zips them into per-round rows — a
  round with hybrid DP active has one user-level entry but as many
  sample-level entries as clients that round, which don't line up 1:1
  by construction.
* **Separate budget policies, applied independently.** See
  [privacy-budget-policies.md](privacy-budget-policies.md) — a budget
  configured for one mechanism never affects another's enforcement.
* **The one deliberate summary that isn't a violation:**
  `PrivacyMetricsSnapshot.sample_epsilon` is the *worst-case (max)*
  epsilon across clients that have submitted a sample-level entry so
  far — a reduction *within* one mechanism (across its own multiple
  entries), not a combination *across* mechanisms. Documented explicitly
  at the struct definition (`run_manager.hpp`) so it's never mistaken
  for the forbidden kind of combination.

## RDP accounting (Mironov 2017/2019, subsampled-Gaussian mechanism)

Both `UserLevelAccountant` (C++ and its legacy-derived Python
counterpart) and Opacus's own accountant implement the same published
formula for one step's Rényi DP at order α:

```
q = sample_rate, σ = noise_multiplier
RDP(α) = (1/(α-1)) · log( Σ_{k=0}^{α} C(α,k) · (1-q)^(α-k) · q^k · exp(k(k-1)/(2σ²)) )
```

with the degenerate `q=1` case reducing to the textbook Gaussian
mechanism, `RDP(α) = α/(2σ²)` — used directly by adaptive clipping's
accountant (no subsampling amplification applies there: every cohort
member already selected for the round contributes exactly one bit to
the over-threshold count).

Composition over `T` steps is a plain sum of per-step RDP; conversion to
(ε,δ)-DP is:

```
ε = min_α [ T · RDP(α) + log(1/δ)/(α-1) ]
```

minimized numerically over a fixed set of candidate orders (C++: 2–64
plus {80,96,128,256,512}, ~69 orders; Opacus: ~151 fractional orders —
see known-limitations.md's Privacy Engineering Phase section for why
this makes the C++/legacy-Python accountant a valid but measurably more
conservative upper bound than Opacus's own).

**Validation, not assumption:** before trusting the legacy
`federated.dp_accountant.MomentsAccountant` (reused rather than
reimplemented — see its own module docstring), its per-order RDP values
were checked against Opacus's `compute_rdp` directly and found to match
to float precision at every shared integer order
(`UserLevelAccountantGoldenParityTests` in
`python/tests/test_privacy_accounting.py`). The C++ implementation is
golden-parity tested against the *same* reference value
(`cpp/core/tests/privacy_test.cpp`'s cross-language parity case: q=0.1,
σ=1.2, 100 steps, δ=1e-5 → ε≈6.414998048146023, matching to 1e-6).
`python/tests/test_privacy_statistical_validation.py` extends this with
parametrized monotonicity checks (more noise ⇒ lower ε; more
subsampling amplification ⇒ lower ε; more steps ⇒ never-decreasing ε)
across many parameter combinations, not just one hand-picked point.

## Trust model

This is central differential privacy, not secure aggregation — the
coordinator sees plaintext client updates before applying user-level
DP's clip+noise step. See
[privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)'s
Section 0 for the full, explicit trust model (trusted coordinator
operator, honestly-reporting workers, non-cryptographic randomness,
unencrypted transport by default) — read that before deploying this
system anywhere the trust model doesn't already hold.
