# Threshold Recovery Interoperability

Access date: July 28, 2026.

## Current Cross-Language Constraint

The repository's secure-aggregation runtime is split across:

- C++ coordinator
- Python worker

That means threshold recovery must either:

- use one interoperable scheme with unambiguous wire semantics on both sides, or
- centralize all threshold operations behind one new trusted runtime boundary

## Interoperability Findings

### Crypto++ vs PyCryptodome

The reviewed public materials did not provide a clean, protocol-ready
interoperability story for the repository's use case. PyCryptodome's
documented Shamir API is fixed to 16-byte secrets/shares, while the
repository would need a more explicit and future-proof encoding contract
than "mix two different secret-sharing implementations and hope the field
semantics align".

### `vsss-rs`

`vsss-rs` could potentially centralize semantics, but only by adding Rust
as a new trusted implementation layer and FFI boundary. That is not a
small interoperability detail; it is an architectural shift.

### `libgfshare`

The reviewed materials did not provide enough assurance to treat this as a
safe interoperability anchor.

## Conclusion

Interoperability is not solved by having "a library somewhere" on each
side. None of the reviewed options provided a low-risk cross-language path
that met this repository's admission bar.
