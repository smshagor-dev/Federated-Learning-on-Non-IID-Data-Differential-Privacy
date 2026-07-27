"""Secure Aggregation Protocol Foundation and No-Dropout Masked-Sum
Core -- Python mirror of the pure-math C++ modules in
cpp/coordinator/include/fl_coordinator/secure_aggregation_encoding.hpp,
secure_aggregation_mask.hpp, and secure_aggregation_session.hpp. See
docs/secure-aggregation-protocol-foundation.md for the design decision
and scope statement this package implements against.

Everything in this package is protocol *math and state*, not a live
network path: no gRPC, no protobuf, no coordinator/worker wiring.
X25519/HKDF/ChaCha20 primitive wrappers are a separate, not-yet-written
module (mirroring the C++ split between the gRPC-gated crypto module
and this non-gRPC-gated one) since they need
docs/secure-aggregation-cryptographic-provider.md's selected
``cryptography``/PyNaCl primitives, not just stdlib arithmetic.
"""
