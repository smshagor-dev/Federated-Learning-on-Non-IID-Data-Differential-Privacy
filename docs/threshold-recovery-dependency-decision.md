# Threshold Recovery Dependency Decision

Decision date: July 28, 2026.

## Final Decision

`NO_ACCEPTABLE_DEPENDENCY_FOUND`

## Reason

No reviewed candidate satisfied all mandatory gates at once:

- vetted enough for threshold-recovery secret handling
- maintainable enough for long-lived adoption
- compatible enough with the repository's current C++ plus Python split
- clear enough in public security and audit posture

## Operational Consequence

The repository must keep dropout recovery blocked.

Allowed after this decision:

- documentation
- architecture preparation
- dependency research
- validation of the existing no-dropout runtime

Not allowed after this decision:

- enabling partial-cohort finalization
- shipping recovery RPCs as active runtime behavior
- claiming complete secure aggregation
- introducing custom Shamir or custom threshold cryptography
