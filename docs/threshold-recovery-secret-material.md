# Threshold Recovery Secret Material

## Material That Would Exist In A Future Recovery Design

- per-session ephemeral X25519 private key
- pairwise shared secrets
- per-peer mask seeds or equivalent recovery inputs
- shares derived from recovery secrets
- survivor-set transcript bindings
- reconstructed dropped-user recovery material at finalize time only

## Mandatory Handling Rules

- Session-scoped only: no reuse across sessions or retries.
- Memory only by default: no plaintext persistence of private keys, shares, or reconstructed recovery material.
- Signed and bound: every transported recovery message must bind `session_id`, `run_id`, `round_id`, participant identity, and survivor-set commitment.
- Short-lived: reconstructed material must exist only long enough to complete finalization or abort.
- Abort on ambiguity: if share counts, participant numbering, or survivor-set commitments disagree, recovery must stop.

## Current Repository Compliance Baseline

The current repository already follows part of this policy for the
no-dropout path:

- worker ephemeral private keys are session-scoped memory only
- session persistence excludes private keys, pairwise secrets, masks, and shares

Threshold recovery must preserve that same discipline rather than weaken
it.
