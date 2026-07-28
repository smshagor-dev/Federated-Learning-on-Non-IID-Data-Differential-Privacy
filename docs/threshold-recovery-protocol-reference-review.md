# Threshold Recovery Protocol Reference Review

Access date: July 28, 2026.

## Primary References Reviewed

| Reference | Relevant claim | Relevance |
|---|---|---|
| [Bonawitz et al., "Practical Secure Aggregation for Privacy-Preserving Machine Learning"](https://pmpml.github.io/PMPML16/papers/PMPML16_paper_8.pdf) | Failure robustness requires secret-sharing-assisted recovery after dropouts. | Canonical baseline for the recovery problem this repository has not yet implemented. |
| [Li et al., "Secure Aggregation for Federated Learning in Flower"](https://arxiv.org/pdf/2205.06117) | A SecAgg/SecAgg+-style design can be exposed in a modern FL framework, but still depends on a concrete dropout-resilient protocol layer. | Useful implementation-shape reference for framework integration, not a dependency source. |

## Evidence Register

| Source | Access date | Claim used | Evidence quality | Uncertainty |
|---|---|---|---|---|
| Bonawitz PDF | July 28, 2026 | Protocol 1 introduces dropped-user recovery using secret sharing after the no-dropout masking baseline fails robustness requirements. | High | Low |
| Flower / Salvia paper | July 28, 2026 | SecAgg/SecAgg+ can be integrated into an FL framework, but still depend on the underlying dropout-resilient protocol. | Medium | Low |

## What The Bonawitz Reference Implies For This Repository

The Bonawitz protocol family assumes more than the repository currently has:

- threshold secret-sharing support
- survivor-set agreement after dropout classification
- recovery messages for dropped users' masking material
- distinct handling for users who drop before versus after masked submission

The repository currently implements only the no-dropout prefix:

- pairwise key establishment
- signed roster freeze
- masked submission
- complete-cohort finalization

That means the codebase is aligned with the early protocol stages, but not
with the failure-robust stages.

## What The Flower / Salvia Reference Adds

The Flower paper is helpful as an architectural confirmation that:

- dropout-robust secure aggregation is feasible in an FL framework
- protocol ergonomics matter
- survivor-threshold logic must be first-class, not bolted on

It is not a sufficient dependency source for this repository because it is
an implementation paper, not a maintained cross-language cryptographic
library suitable for direct embedding here.

## Review Conclusion

The literature confirms that threshold recovery is a real, protocol-level
requirement for dropout-robust secure aggregation. It does not remove the
need for a vetted, maintainable, interoperable dependency stack for this
repository's current C++ coordinator and Python worker split.
