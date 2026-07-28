# Threshold Recovery Survivor Sets

## Why Survivor Sets Matter

Dropout recovery is only safe if all honest survivors operate on the same
view of:

- who advertised
- who submitted masked updates
- who dropped before masked submission
- who dropped after masked submission

If those sets diverge, a coordinator can potentially extract more
information than the protocol intends.

## Future Requirements

- survivor sets must be transcript-bound and signed
- recovery share release must be conditioned on the same survivor-set commitment
- the coordinator must never be trusted to provide inconsistent survivor sets without detection

## Current State

The repository has no survivor-set protocol today because it aborts on any
dropout before finalization. That keeps the current confidentiality story
simple and honest.
