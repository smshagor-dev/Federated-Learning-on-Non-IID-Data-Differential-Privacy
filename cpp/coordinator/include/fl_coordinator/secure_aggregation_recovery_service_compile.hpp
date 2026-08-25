#pragma once

// Compile-safety shim for the v3 live recovery service.
//
// The service is gRPC/OpenSSL-gated like the rest of the coordinator transport
// adapter. Keep platform headers here so the implementation header remains
// focused on protocol logic. OpenSSL exposes BN_zero as a void-style macro on
// supported releases, while the first recovery implementation intentionally
// treats zero-initialization as a checked operation. Map that call to
// BN_set_word(..., 0), which has the same effect and a real success return.

#include <chrono>
#include <cctype>
#include <iterator>

#include <openssl/bn.h>

#ifdef BN_zero
#undef BN_zero
#endif
#define BN_zero(value) BN_set_word((value), 0)

#include "fl_coordinator/secure_aggregation_recovery_service.hpp"

#undef BN_zero
