# Threshold Recovery Supply-Chain Review

Access date: July 28, 2026.

## Review Method

This review used primary project materials only:

- official documentation
- official repository pages
- repository tags and security-policy pages

## Supply-Chain Findings

| Candidate | Primary sources | Maintenance / release signal | Security signal | Supply-chain concern |
|---|---|---|---|---|
| Crypto++ | [repo](https://github.com/weidai11/cryptopp), [security policy](https://github.com/weidai11/cryptopp/security) | Strong upstream project, visible versioning, active security policy page | Better than the other candidates reviewed | Only solves the C++ half directly; would force an unproven mixed-language stack |
| PyCryptodome | [docs](https://pycryptodome.readthedocs.io/en/v3.23.0/src/protocol/ss.html), [security page](https://github.com/Legrandin/pycryptodome/security) | Strong Python project with clear tagged releases | No repository security policy page detected | Secret-sharing module shape is too constrained for this use case |
| `vsss-rs` | [repo](https://github.com/mikelodder7/vsss-rs), [docs.rs](https://docs.rs/vsss-rs/latest/vsss_rs/) | Active tags visible | Audit messaging is mixed in public docs/README | Requires introducing Rust as a new trusted toolchain and FFI surface |
| `libgfshare` | [reviewed fork](https://github.com/djpohly/libgfshare) | Weak release and governance signal in reviewed fork | No stronger security posture surfaced in primary materials reviewed | Too weak for a new threshold-recovery dependency |

## Evidence Register

| Candidate | Access date | Version / tag | Evidence quality | Uncertainty |
|---|---|---|---|---|
| Crypto++ | July 28, 2026 | `CRYPTOPP_8_9_0` / 8.9 | High | Low |
| PyCryptodome | July 28, 2026 | `v3.23.0` / docs 3.23.0 | High | Low |
| `vsss-rs` | July 28, 2026 | `v5.4.0` | Medium | Medium |
| `libgfshare` | July 28, 2026 | no release tags surfaced in reviewed fork | Low to medium | High |

## Adoption Standard Applied

Because threshold recovery would handle masking secrets that directly
govern secure-aggregation confidentiality, the dependency standard here is
closer to "cryptographic primitive admission" than to ordinary helper
library admission.

That means the repository should prefer:

- one clearly maintained dependency stack
- minimal cross-language translation ambiguity
- explicit version/tag provenance
- a clean security disclosure story

## Result

The reviewed options do not currently clear that bar for this codebase.
