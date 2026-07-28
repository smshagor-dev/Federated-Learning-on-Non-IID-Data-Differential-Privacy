# Secure Aggregation Threat Model

**Status: the repository now has a real, validated secure-aggregation
runtime, but only for the no-dropout provider
`SECAGG_NO_DROPOUT_EXPERIMENTAL`. This document therefore has two jobs:
describe the guarantees the current implementation really provides, and
state the stronger threat model that a future threshold-recovery path
would need to satisfy. Dropout recovery remains blocked pending the
dependency decision in
[threshold-recovery-dependency-decision.md](threshold-recovery-dependency-decision.md).**

## Mandatory trust statement

Secure aggregation is a narrow, specific guarantee. It is not a general
federated-learning security solution. Stated plainly, with today's
implemented provider `SECAGG_NO_DROPOUT_EXPERIMENTAL`:

* It hides individual client updates from the coordinator.
* It does **not** prevent model poisoning.
* It does **not** prove that a worker trained honestly.
* It does **not** verify a worker-reported sample-level epsilon.
* It does **not** prove that a worker clipped its update correctly.
* It does **not** prevent Sybil clients unless identity admission is
  separately controlled (worker identity/certificate issuance is a
  distinct, deferred control — see
  [worker-identity.md](worker-identity.md) once written).
* It does **not** protect personalized checkpoints stored on workers —
  those never enter the secure aggregation path at all (see
  [personalized-model-store.md](personalized-model-store.md)).
* It does **not** make a malicious model safe — a coalition large
  enough to control the aggregate can still steer training, secure
  aggregation only stops the *coordinator* from reading individual
  contributions.
* It does **not** replace TLS or worker authentication — those are
  separate, complementary controls this category also implements.
* It does **not** provide Byzantine robustness — no outlier-rejection
  or robust-aggregation rule is part of this protocol.
* It protects only the aggregation payload actually covered by the
  protocol (masked update tensors and, as implemented now, aggregated
  adaptive-clipping indicators); it says nothing about metadata,
  timing, task assignment, or any other channel.
* Its current guarantee is conditional on full cohort completion. A
  dropped or missing participant causes abort rather than recovery.
  There is no threshold reconstruction path today.

This project does not claim malicious-client-secure user-level DP.
Client-side clipping under secure aggregation is trusted, not verified
— see [secure-privacy-integration.md](secure-privacy-integration.md)
once written, and the Mode B/C description in the architecture doc.
Verifiable clipping (a client proving in zero-knowledge or otherwise
that it clipped correctly, without revealing its update) is explicitly
out of scope for this category.

## Actors and their assumed capabilities

| Actor | Assumption |
|---|---|
| **Coordinator (honest-but-curious)** | Follows the protocol correctly (routes messages, runs the state machine as specified) but may attempt to learn information from anything it legitimately observes — message metadata, timing, plaintext aggregates, ledger contents. This is the actor the secure aggregation protocol is built to defend against for individual-update confidentiality. |
| **Honest clients** | Follow the protocol, generate real ephemeral keys, submit real masked updates, and in a future recovery design would honor recovery-share rules correctly. |
| **Malicious clients (individual)** | May submit malformed messages, replay old messages, forge protocol fields, attempt early aborts, submit a spoofed update, or attempt to poison the model via a legitimate-looking but adversarial update. Signature/replay/session checks defend against forgery and replay; nothing in this protocol defends against poisoning via a validly-signed, validly-masked, adversarial update — that is a model-poisoning problem, explicitly out of scope. |
| **Colluding clients** | In the current no-dropout implementation, colluding clients can still poison the model but do not get a recovery-share path because none exists. For a future threshold-recovery design, the guarantee would hold only up to the configured reconstruction threshold. |
| **Compromised worker** | A worker whose host has been fully compromised (private key extracted, arbitrary code execution) can do anything any client can do, plus impersonate that specific worker's identity until the compromise is detected and the worker's key is revoked (see [key-management.md](key-management.md)). Detection of compromise itself is out of scope — this project provides revocation as a response mechanism, not intrusion detection. |
| **Compromised coordinator** | If the coordinator's own process/host is compromised beyond honest-but-curious (e.g., an attacker with code-execution on the coordinator, not just passive observation), most guarantees this protocol provides do not hold — a fully malicious coordinator could, for example, selectively lie about cohort membership or manipulate the state machine before signature/transcript checks catch it. Signed envelopes and transcript hashing raise the cost and improve detectability of such tampering but this project does not claim a fully Byzantine-coordinator-safe protocol. |
| **Network attacker** | Assumed present and hostile on the network path between every service. Defended against via mTLS (transport confidentiality/integrity/mutual authentication) once implemented — plaintext gRPC remains explicitly development-only. |
| **Replay attacker** | An attacker (network-level or a malicious participant) that captures and re-sends a previously valid message. Defended against via nonces, monotonic sequence numbers, and session/round/model-version binding in every signed envelope. |
| **Sybil attacker** | An attacker registering many worker identities to gain disproportionate cohort influence. Only defended against to the extent identity *admission* is controlled — this project's worker identity registry (deferred) authenticates a claimed identity's key ownership and signing capability, not real-world uniqueness of the human/organization behind it; operator-level admission policy is a deployment concern, not something the protocol itself can solve. |
| **Dropout attacker** | A client that deliberately drops out at a specific protocol stage. **Current behavior:** the session aborts rather than attempts recovery. **Future requirement:** if recovery is ever added, per-dropout-class recovery must not reconstruct an active client's private material. |
| **Protocol abort attacker** | A client or network condition that forces repeated session aborts to deny service or to probe protocol behavior across many sessions. Defended against via bounded per-stage deadlines and session-scoped (never cross-session) key material — an aborted session leaks nothing about a subsequent fresh session's keys. Explicit denial-of-service resilience beyond bounded deadlines is not a goal of this category. |
| **Poisoning attacker** | A client (or coalition) that submits a validly-authenticated, validly-masked update crafted to bias the trained model. **Not addressed by secure aggregation.** This is stated repeatedly in this document deliberately, because it is the single most common secure-aggregation misconception this project must not reproduce. |
| **Storage attacker** | An attacker with read access to coordinator or worker disk/checkpoint storage. Defended against by the stated persistence policy: no plaintext ephemeral private keys, decrypted secret shares, pairwise seeds, or private masks are persisted; if encrypted protocol-state persistence is ever implemented, it must use authenticated encryption under a dedicated key-encryption key (see [protocol-transcript.md](protocol-transcript.md) once written). Personalized model checkpoints on worker disks are a pre-existing, separate risk documented in [known-limitations.md](known-limitations.md) and are not newly addressed by this category. |

## Initial protocol security profile

```text
Coordinator:
  honest-but-curious during protocol execution

Clients:
  authenticated (once worker identity lands)
  potentially offline or dropping
  not assumed Byzantine-safe

Network:
  hostile
  protected using mTLS (once implemented)

Collusion:
  current implementation has no threshold-recovery path
  future recovery would be bounded by the configured reconstruction threshold

Model poisoning:
  not prevented

Verifiable clipping:
  not implemented
```

## Attack disposition summary

| Attack | Disposition |
|---|---|
| Coordinator reading an individual client's plaintext update via the secure path | **Prevented** in the implemented no-dropout secure path |
| Network eavesdropping/tampering | **Prevented** on the mTLS-secured runtime paths used by current validation |
| Message replay | **Prevented** by the implemented signed-envelope replay/sequence checks |
| Forged/tampered protocol message | **Prevented** by the implemented signing and binding checks |
| A worker impersonating another worker's identity without key compromise | **Prevented** by the implemented identity/signing-key validation path |
| A compromised worker's key being used after revocation | **Prevented** by the implemented revocation and key-resolution path |
| Coalition below a future reconstruction threshold learning an individual update | **Deferred** — no threshold recovery is live today |
| Coalition at or above a future reconstruction threshold learning protected material | **Deferred / not addressed** by a future recovery design |
| A dropped-out client's private material being exposed through the recovery path | **Deferred** — no recovery path exists today |
| Excessive dropout silently producing a degraded/wrong aggregate | **Prevented today by aborting instead of partial finalization** |
| Model poisoning via a validly-authenticated update | **Not addressed** |
| A worker lying about having clipped its update (Mode B/C) | **Not addressed** — trusted, not verified; see the mandatory trust statement above |
| A worker misreporting its sample-level epsilon | **Partially mitigated** — the coordinator can check monotonicity and configuration-hash consistency of reported values (see the sample-level budget enforcement work in this pass) but cannot independently verify the true accountant state inside the worker process |
| Sybil registration of many worker identities | **Partially mitigated** — identity issuance can be gated by an operator, but no cryptographic proof of real-world uniqueness is provided |
| A fully compromised coordinator host tampering with protocol logic beyond passive observation | **Partially mitigated** (signed envelopes and transcript hashing improve detectability) — **not fully addressed** |
| Denial of service via repeated session aborts | **Partially mitigated** (bounded per-stage deadlines) — **not addressed** as a dedicated goal |
| Hardware-level compromise / lack of remote attestation of worker code | **Deferred** — explicitly out of scope for this category (see the Mandatory Trust Statement's note that signed self-reporting authenticates the source, not the executing code) |
| Storage-level compromise of coordinator/worker disks | **Partially mitigated** by the no-plaintext-secret persistence policy — checkpoint/disk encryption at the OS/infrastructure level remains the operator's responsibility |

## Relationship to Differential Privacy's existing trust model

This threat model is additive to, not a replacement for,
[privacy-mathematics.md](privacy-mathematics.md)'s trust model and
[privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)'s
Section 0. Central differential privacy (user-level DP, adaptive
clipping) already assumes a trusted-but-curious coordinator that
correctly applies clip+noise; secure aggregation changes *what the
coordinator can see*, not whether workers are trusted to have trained
or clipped honestly. The two systems' trust assumptions must be read
together, not independently, once secure aggregation and DP are
combined. For the recovery-specific future state, see
[threshold-recovery-threat-model.md](threshold-recovery-threat-model.md).
