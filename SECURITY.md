# Security Policy

## Overview

The **Federated Learning on Non-IID Data with Differential Privacy** repository contains research and engineering components related to:

- Federated learning
- Differential privacy
- Privacy accounting
- Client sampling
- Secure aggregation
- PKI and certificate handling
- Signed messages and capability statements
- gRPC and Protocol Buffers
- Python, C++20, and Go services
- Docker and Docker Compose infrastructure
- Desktop experiment orchestration

Security reports are welcome and should be made responsibly.

This repository is a research platform. It is not formally security audited, not production certified, and not evidence of regulatory compliance.

## Supported Versions

Security fixes are normally applied to:

| Version | Supported |
|---|---|
| Current `main` branch | Yes |
| Latest tagged release, when one exists | Best effort |
| Older commits or historical branches | No |
| Third-party forks | No |
| Modified or independently deployed versions | No |

A report may still be useful when it affects an older version, but maintainers may require reproduction against the current `main` branch.

## Reporting a Vulnerability

**Do not open a public GitHub issue for an undisclosed vulnerability.**

Use one of the following private methods:

1. Use GitHub's private vulnerability-reporting or security-advisory interface for this repository when available.
2. Contact the repository maintainer privately through the contact information published on the maintainer's GitHub profile.
3. When no private channel is available, send only a minimal request for a secure reporting channel. Do not include exploit details publicly.

Project maintainer:

**Md Shahanur Islam Shagor**  
GitHub: [smshagor-dev](https://github.com/smshagor-dev)

## What to Include

A useful vulnerability report should include:

- A concise title
- Affected component and file paths
- Affected branch, release, or commit SHA
- Vulnerability class
- Preconditions
- Step-by-step reproduction
- Proof-of-concept code or input, when safe
- Expected and actual behavior
- Security impact
- Potential attack scenario
- Suggested mitigation, when available
- Operating system and toolchain details
- Relevant logs with secrets removed
- Whether the issue is already public
- Whether a CVE has been requested or assigned

For cryptographic or privacy-related findings, also include the relevant threat model and assumptions.

## Response Process

The maintainer will make a reasonable effort to:

1. Acknowledge a complete report
2. Confirm whether the issue can be reproduced
3. Assess scope, severity, and affected components
4. Request additional information when necessary
5. Develop and validate a correction
6. Coordinate disclosure when appropriate
7. Credit the reporter unless anonymity is requested

Response and remediation times depend on severity, reproducibility, maintainer availability, project scope, and the complexity of cross-language validation.

No fixed service-level agreement is guaranteed.

## Coordinated Disclosure

Please allow a reasonable remediation period before public disclosure.

Public disclosure should be coordinated when possible so that:

- A fix or mitigation is available
- Affected users can update
- Security-sensitive details are not exposed prematurely
- Release notes accurately describe the impact
- Credit and attribution are handled correctly

The maintainer may request additional time for issues involving cryptography, secure aggregation, distributed protocols, or multi-language runtime changes.

## In-Scope Vulnerabilities

Examples include:

- Authentication or authorization bypass
- Certificate validation failures
- Private-key or secret exposure
- Signature verification bypass
- Replay attacks
- Message-integrity failures
- Unsafe deserialization
- Command injection
- Path traversal
- Arbitrary file access
- Remote code execution
- Container escape caused by repository configuration
- Insecure default network exposure
- Dependency confusion or unsafe package resolution
- Sensitive data leakage through logs or artifacts
- Secure aggregation failures that reveal protected updates
- Privacy-accounting errors that materially understate privacy loss
- Clipping or noise-application errors that invalidate documented privacy assumptions
- Cross-client data exposure
- Malicious model-update handling weaknesses
- Denial-of-service conditions with clear security impact
- CI or release-pipeline weaknesses that enable unauthorized code or artifact modification
- Tracked credentials, private keys, or high-confidence secrets

## Privacy-Specific Reports

A differential privacy concern should explain:

- Whether the issue is client-level or sample-level
- The adjacency definition
- Sampling strategy
- Clipping mechanism
- Noise mechanism and scale
- Accountant and composition method
- Target delta
- Number of rounds
- Whether deterministic noise was enabled
- The difference between the documented and actual guarantee

An epsilon value by itself is not sufficient to establish a vulnerability.

Reports should demonstrate a mismatch between implementation, documentation, configuration, or privacy assumptions.

## Secure Aggregation and Cryptographic Reports

For cryptographic findings, include:

- Protocol stage
- Attacker capability
- Key or certificate assumptions
- Participant corruption threshold
- Replay or ordering assumptions
- Randomness requirements
- Message transcript or minimal test vector
- Whether confidentiality, integrity, authenticity, or availability is affected

Do not include real private keys, production credentials, or data belonging to other people.

## Out-of-Scope Reports

The following are generally out of scope unless they demonstrate concrete security impact:

- Missing security headers on a purely local or non-deployed development interface
- Reports based only on automated scanner output
- Dependency-version reports without an exploitable path
- Self-XSS
- Social engineering
- Physical attacks
- Denial of service requiring unrealistic resources
- Problems only present after disabling documented protections
- Attacks requiring prior full administrative control
- Publicly known vulnerabilities that do not affect the repository
- Issues in unsupported forks or modified deployments
- Theoretical privacy criticism without an implementation or documentation mismatch
- Claims that CI success should imply production certification
- Requests for production guarantees that the project does not claim
- Spam, extortion, or threats

## Security Testing Rules

Good-faith security research must:

- Use systems and data you own or are authorized to test
- Avoid accessing another person's data
- Avoid service disruption
- Avoid destructive testing
- Avoid persistence
- Avoid secret extraction beyond the minimum needed to prove impact
- Stop testing when sensitive data is encountered
- Keep vulnerability details confidential during coordination
- Comply with applicable law

The project does not authorize testing against third-party systems, cloud accounts, deployed services, or infrastructure not owned by the reporter.

## Secrets and Sensitive Data

Never commit:

- Passwords
- API keys
- Access tokens
- Private keys
- Production certificates
- Cloud credentials
- Database connection secrets
- Personal data
- Proprietary datasets
- Unpublished vulnerability details

The repository CI includes pattern-based scanning and PKI checks, but no automated scanner guarantees complete detection.

If a secret is committed:

1. Revoke or rotate it immediately
2. Remove it from the current tree
3. Assess Git history exposure
4. Notify affected parties privately
5. Do not assume deleting the file makes the secret safe

## Dependency Security

When reporting a vulnerable dependency, include:

- Package name and version
- Advisory or CVE identifier
- Affected runtime path
- Whether the dependency is reachable
- Minimal exploitation conditions
- Available fixed version
- Compatibility impact of upgrading

Reports based only on package presence may be closed when the vulnerable code path is not used.

## Security Boundaries and Non-Claims

A passing CI workflow does not establish:

- Formal security certification
- Regulatory compliance
- Production readiness
- Correctness under every threat model
- Resistance to all poisoning or inference attacks
- Complete secret detection
- Real-world multi-device deployment validation
- Universal differential privacy guarantees

The active root runtime is primarily a controlled single-machine simulation. Auxiliary security and distributed components use separate execution paths and should be evaluated according to their actual integration and deployment state.

## Public Issues After Disclosure

After a vulnerability has been fixed or responsibly disclosed, a public issue may be created for:

- Non-sensitive follow-up work
- Documentation
- Hardening
- Regression testing
- Architectural improvements

Do not copy confidential exploit details into a public issue unless disclosure has been coordinated.

## Recognition

Valid reports may be acknowledged in release notes, security advisories, or repository documentation.

Reporters may request anonymity.

## Safe-Harbor Intent

The project supports good-faith research performed within this policy.

The maintainer intends not to pursue action against researchers who:

- Follow this policy
- Avoid privacy violations and service disruption
- Report findings promptly
- Provide reasonable time for remediation
- Do not exploit findings for personal gain beyond responsible disclosure

This statement does not authorize testing of third-party systems and does not override applicable law.
