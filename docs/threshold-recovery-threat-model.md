# Threshold Recovery Threat Model

Access date: July 28, 2026.

## Status

This document defines the threat model for a future dropout-recovery
extension on top of the repository's current no-dropout secure
aggregation runtime. No live threshold-recovery path is enabled today.

## Security Goal

If threshold recovery is ever added, it must preserve these guarantees:

- the coordinator still cannot decode an individual surviving client's update
- a dropped client's recovery path cannot reveal an active client's private mask material
- a partial cohort never silently degrades into an insecure aggregate
- survivor sets are consistent across all honest surviving clients
- replayed, cross-session, or cross-round shares are rejected

## Adversaries Specific To Recovery

| Actor | Required disposition |
|---|---|
| Honest-but-curious coordinator | May orchestrate share relay and recovery, but must learn only the final aggregate and the minimum metadata needed to complete the protocol. |
| Dropped client | May disappear after advertising keys or after sending masked data. Recovery must tolerate the dropout without exposing survivors' private state. |
| Lying coordinator | May report inconsistent survivor sets to different clients. A future design must cryptographically bind the survivor set used for recovery. |
| Colluding clients | A coalition smaller than the reconstruction threshold must not reconstruct protected material. A coalition at or above threshold is outside the protocol guarantee and must be treated as a configuration failure domain, not as a runtime surprise. |
| Replay attacker | May replay old recovery shares or old survivor-set claims. Recovery messages must be session-bound, round-bound, and sequence-protected. |
| Storage attacker | May read coordinator or worker disks. No decrypted recovery shares, seed material, or reconstructed masks may persist in plaintext. |

## Trust Boundaries

Threshold recovery would add three new trust-sensitive boundaries:

1. Share generation at the worker.
2. Share transport through the coordinator.
3. Share reconstruction at complete survivor threshold only.

Each boundary must be authenticated and transcript-bound. None may rely on
opaque best-effort behavior from an unvetted dependency.

## Non-Goals

Threshold recovery still would not:

- prevent poisoning by validly authenticated malicious clients
- prove that a worker trained or clipped honestly
- solve Sybil admission
- protect personalized checkpoints
- make a fully malicious coordinator safe

## Mandatory Failure Policy

If any of the following cannot be proven for a candidate dependency or
integration design, the protocol must abort instead of producing an
aggregate:

- threshold and participant numbering are consistent across languages
- shares are bound to exactly one session and one survivor set
- reconstruction happens only after minimum-survivor checks pass
- recovery material never persists outside the declared policy

## Current Operational Decision

Until a vetted dependency passes the evaluation gates in
[threshold-recovery-dependency-decision.md](threshold-recovery-dependency-decision.md),
the repository remains in no-dropout mode only.
