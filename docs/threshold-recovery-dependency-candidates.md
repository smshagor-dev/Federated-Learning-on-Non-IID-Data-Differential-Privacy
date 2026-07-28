# Threshold Recovery Dependency Candidates

Access date: July 28, 2026.

## Evaluation Gates

A candidate or candidate stack had to satisfy all of the following to be
approved for experimental integration:

- active maintenance signal
- clear version/tag provenance
- compatible licensing
- acceptable security posture and disclosure process
- no custom threshold-crypto implementation required by this repository
- workable interoperability across the repository's C++ and Python boundary
- support for the secret sizes and share semantics needed for dropout recovery

## Candidates Reviewed

| Candidate | Source | Access date | Version / tag reviewed | Relevant claim used | Evidence quality | Uncertainty | Outcome |
|---|---|---|---|---|---|---|---|
| Crypto++ `SecretSharing` | [repo](https://github.com/weidai11/cryptopp), [class docs](https://cryptopp.com/docs/ref/class_secret_sharing.html), [security policy](https://github.com/weidai11/cryptopp/security) | July 28, 2026 | `CRYPTOPP_8_9_0` / Crypto++ 8.9 | Built-in Shamir support exists and the project publishes a security policy. | High for existence/maintenance, medium for protocol fit | Medium: public docs do not by themselves solve the worker-side interoperability problem | Rejected as standalone and as half of a mixed-language stack |
| PyCryptodome `Shamir` | [docs](https://pycryptodome.readthedocs.io/en/v3.23.0/src/protocol/ss.html), [security page](https://github.com/Legrandin/pycryptodome/security) | July 28, 2026 | docs 3.23.0, repo tag `v3.23.0` | Secret and share are documented as 16 bytes; incorrect shares can reconstruct the wrong secret unless an external authentication mechanism is used. | High | Low | Rejected as a direct protocol fit |
| `vsss-rs` | [repo](https://github.com/mikelodder7/vsss-rs), [docs.rs](https://docs.rs/vsss-rs/latest/vsss_rs/) | July 28, 2026 | tag `v5.4.0` | Supports Shamir plus Feldman/Pedersen-style VSS and advertises constant-time goals, but public audit messaging is mixed. | Medium | Medium | Rejected with blockers |
| `libgfshare` family | [reviewed fork](https://github.com/djpohly/libgfshare) | July 28, 2026 | no release tags surfaced from the reviewed fork | Focused secret-sharing library exists, but maintenance/release provenance is weak in the reviewed materials. | Low to medium | High | Rejected |

## Candidate Notes

### Crypto++ `SecretSharing`

Pros:

- existing C++ dependency candidate
- upstream project is large and maintained
- secret sharing is a documented built-in feature

Cons:

- no repository-native Python peer with the same wire format and transcript semantics was identified
- adopting Crypto++ on the coordinator and something else on the worker would create a new interoperability risk surface
- the current repository needs a protocol dependency, not just a math primitive on one side

### PyCryptodome `Shamir`

Pros:

- maintained Python crypto library
- official docs clearly define the API

Cons:

- docs state the secret is exactly 16 bytes and each share is exactly 16 bytes
- docs explicitly warn that reconstruction can succeed with the wrong secret if a bad share is presented unless an external authentication mechanism is added
- this is not enough by itself for a cross-language dropout-recovery protocol

### `vsss-rs`

Pros:

- supports Shamir plus Feldman and Pedersen styles
- states constant-time goals
- richer feature set than the C++ and Python candidates above

Cons:

- repository split is C++ plus Python, not Rust
- moving to Rust FFI for both sides would be a substantial platform change
- public materials simultaneously say the library "has received a few audits" and that it is "currently under audit" with results pending, which is not clean enough for this gate

### `libgfshare`

Pros:

- focused secret-sharing library

Cons:

- weak release metadata
- low maintenance signal relative to the risk being introduced
- no evidence strong enough to justify making it the basis for a new secure-aggregation recovery path

## Candidate Conclusion

No candidate met all mandatory gates for this repository's current
language split and security posture.
