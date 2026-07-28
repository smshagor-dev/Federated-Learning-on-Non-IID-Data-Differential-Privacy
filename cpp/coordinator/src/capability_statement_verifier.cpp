#include "fl_coordinator/capability_statement_verifier.hpp"

#include "worker/worker.pb.h"

#include <openssl/evp.h>

#include <array>
#include <charconv>
#include <cstdint>
#include <sstream>
#include <vector>

namespace fl::coordinator {

namespace {

// -- JSON string escaping, matching Python's json.dumps(ensure_ascii=True) --
//
// Python's encoder: '"' -> \", '\\' -> \\\\, and the C0 control range
// 0x00-0x1F either via the six short escapes (\b \f \n \r \t -- note
// there is no short escape for 0x0B) or generic \u00XX; any code point
// > 0x7F (this function decodes the input as UTF-8 to find those) is
// escaped as \uXXXX (a surrogate pair for code points above 0xFFFF).
// '/' is left unescaped, matching Python's default.
void append_u_escape(std::string& out, std::uint32_t code_unit) {
    static constexpr char kHex[] = "0123456789abcdef";
    out += "\\u";
    out += kHex[(code_unit >> 12) & 0xF];
    out += kHex[(code_unit >> 8) & 0xF];
    out += kHex[(code_unit >> 4) & 0xF];
    out += kHex[code_unit & 0xF];
}

std::string json_escape_string(const std::string& utf8) {
    std::string out;
    out.reserve(utf8.size() + 2);
    out += '"';
    std::size_t i = 0;
    while (i < utf8.size()) {
        const unsigned char byte0 = static_cast<unsigned char>(utf8[i]);
        if (byte0 == '"') {
            out += "\\\"";
            ++i;
        } else if (byte0 == '\\') {
            out += "\\\\";
            ++i;
        } else if (byte0 == '\n') {
            out += "\\n";
            ++i;
        } else if (byte0 == '\r') {
            out += "\\r";
            ++i;
        } else if (byte0 == '\t') {
            out += "\\t";
            ++i;
        } else if (byte0 == '\b') {
            out += "\\b";
            ++i;
        } else if (byte0 == '\f') {
            out += "\\f";
            ++i;
        } else if (byte0 < 0x20) {
            append_u_escape(out, byte0);
            ++i;
        } else if (byte0 < 0x80) {
            out += static_cast<char>(byte0);
            ++i;
        } else {
            // Decode one UTF-8 code point so it can be re-emitted as
            // \uXXXX (ensure_ascii=True). Malformed/truncated UTF-8 is
            // treated byte-by-byte as Latin-1 rather than throwing --
            // this function must never crash on attacker-controlled
            // input; a malformed sequence simply fails to round-trip
            // through canonicalization, which fails signature
            // verification downstream rather than corrupting memory.
            std::uint32_t code_point = 0;
            int extra_bytes = 0;
            if ((byte0 & 0xE0) == 0xC0) {
                code_point = byte0 & 0x1F;
                extra_bytes = 1;
            } else if ((byte0 & 0xF0) == 0xE0) {
                code_point = byte0 & 0x0F;
                extra_bytes = 2;
            } else if ((byte0 & 0xF8) == 0xF0) {
                code_point = byte0 & 0x07;
                extra_bytes = 3;
            } else {
                append_u_escape(out, byte0);
                ++i;
                continue;
            }
            if (i + static_cast<std::size_t>(extra_bytes) >= utf8.size()) {
                append_u_escape(out, byte0);
                ++i;
                continue;
            }
            bool valid_continuation = true;
            for (int k = 1; k <= extra_bytes; ++k) {
                const unsigned char continuation = static_cast<unsigned char>(utf8[i + k]);
                if ((continuation & 0xC0) != 0x80) {
                    valid_continuation = false;
                    break;
                }
                code_point = (code_point << 6) | (continuation & 0x3F);
            }
            if (!valid_continuation) {
                append_u_escape(out, byte0);
                ++i;
                continue;
            }
            if (code_point > 0xFFFF) {
                const std::uint32_t adjusted = code_point - 0x10000;
                append_u_escape(out, 0xD800 + (adjusted >> 10));
                append_u_escape(out, 0xDC00 + (adjusted & 0x3FF));
            } else {
                append_u_escape(out, code_point);
            }
            i += static_cast<std::size_t>(extra_bytes) + 1;
        }
    }
    out += '"';
    return out;
}

// Matches Python's float JSON encoding (repr()-style shortest
// round-trip representation) for the realistic range this project's
// timestamps fall in (Unix seconds -- always well inside the fixed-
// notation range both encoders use). Not a fully general JSON-float
// encoder: extreme magnitudes where Python's repr and libstdc++'s
// std::to_chars might choose different fixed-vs-scientific thresholds
// are not exercised by issued_at/expires_at in practice and are not
// claimed to be handled -- see docs/canonical-security-serialization.md.
std::string json_double(double value) {
    std::array<char, 64> buffer{};
    const auto result = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value);
    std::string text(buffer.data(), result.ptr);
    const bool looks_integral =
        text.find_first_of(".eEnN") == std::string::npos;  // no '.', no exponent, not nan/inf
    if (looks_integral) {
        text += ".0";
    }
    return text;
}

std::string json_string_array(const google::protobuf::RepeatedPtrField<std::string>& values) {
    std::string out = "[";
    for (int i = 0; i < values.size(); ++i) {
        if (i > 0) {
            out += ",";
        }
        out += json_escape_string(values.Get(i));
    }
    out += "]";
    return out;
}

std::string json_bool(bool value) {
    return value ? "true" : "false";
}

std::vector<std::uint8_t> hex_decode(const std::string& hex, bool& ok) {
    ok = true;
    if (hex.size() % 2 != 0) {
        ok = false;
        return {};
    }
    std::vector<std::uint8_t> bytes;
    bytes.reserve(hex.size() / 2);
    auto nibble = [&](char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        const int high = nibble(hex[i]);
        const int low = nibble(hex[i + 1]);
        if (high < 0 || low < 0) {
            ok = false;
            return {};
        }
        bytes.push_back(static_cast<std::uint8_t>((high << 4) | low));
    }
    return bytes;
}

std::string hex_encode(const unsigned char* data, std::size_t length) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(length * 2);
    for (std::size_t i = 0; i < length; ++i) {
        out += kHex[(data[i] >> 4) & 0xF];
        out += kHex[data[i] & 0xF];
    }
    return out;
}

std::string sha256_hex(const std::string& message) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_len = 0;
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr);
    EVP_DigestUpdate(ctx, message.data(), message.size());
    EVP_DigestFinal_ex(ctx, digest, &digest_len);
    EVP_MD_CTX_free(ctx);
    return hex_encode(digest, digest_len);
}

bool ed25519_verify(const std::vector<std::uint8_t>& public_key,
                    const std::vector<std::uint8_t>& signature,
                    const std::string& message) {
    if (public_key.size() != 32 || signature.size() != 64) {
        return false;
    }
    EVP_PKEY* pkey = EVP_PKEY_new_raw_public_key(
        EVP_PKEY_ED25519, nullptr, public_key.data(), public_key.size());
    if (pkey == nullptr) {
        return false;
    }
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    bool ok = false;
    if (EVP_DigestVerifyInit(ctx, nullptr, nullptr, nullptr, pkey) == 1) {
        ok = EVP_DigestVerify(ctx,
                              signature.data(),
                              signature.size(),
                              reinterpret_cast<const unsigned char*>(message.data()),
                              message.size()) == 1;
    }
    EVP_MD_CTX_free(ctx);
    EVP_PKEY_free(pkey);
    return ok;
}

}  // namespace

std::string canonical_capability_payload_json(
    const fl::worker::v1::SignedCapabilityStatement& statement) {
    // Field order is the ALPHABETICAL sort of the field names (matching
    // Python's json.dumps(sort_keys=True)), not declaration order --
    // verified against a real Python-produced golden vector in
    // capability_statement_verifier_test.cpp.
    std::ostringstream out;
    out << "{";
    out << "\"build_id\":" << json_escape_string(statement.build_id()) << ",";
    out << "\"cpu_count\":" << statement.cpu_count() << ",";
    out << "\"expires_at\":" << json_double(statement.expires_at()) << ",";
    out << "\"gpu_available\":" << json_bool(statement.gpu_available()) << ",";
    out << "\"gpu_count\":" << statement.gpu_count() << ",";
    out << "\"issued_at\":" << json_double(statement.issued_at()) << ",";
    out << "\"maximum_private_batch_size\":" << statement.maximum_private_batch_size() << ",";
    out << "\"maximum_task_bytes\":" << statement.maximum_task_bytes() << ",";
    out << "\"nonce\":" << json_escape_string(statement.nonce()) << ",";
    out << "\"opacus_version\":" << json_escape_string(statement.opacus_version()) << ",";
    out << "\"schema_version\":" << statement.schema_version() << ",";
    out << "\"secure_random_available\":" << json_bool(statement.secure_random_available()) << ",";
    out << "\"signing_key_id\":" << json_escape_string(statement.signing_key_id()) << ",";
    out << "\"software_version\":" << json_escape_string(statement.software_version()) << ",";
    out << "\"supported_accountants\":" << json_string_array(statement.supported_accountants())
        << ",";
    out << "\"supported_algorithms\":" << json_string_array(statement.supported_algorithms())
        << ",";
    out << "\"supported_clipping_modes\":"
        << json_string_array(statement.supported_clipping_modes()) << ",";
    out << "\"supported_model_schema_hashes\":"
        << json_string_array(statement.supported_model_schema_hashes()) << ",";
    out << "\"supported_models\":" << json_string_array(statement.supported_models()) << ",";
    out << "\"supported_privacy_modes\":" << json_string_array(statement.supported_privacy_modes())
        << ",";
    out << "\"worker_id\":" << json_escape_string(statement.worker_id());
    out << "}";
    return out.str();
}

CapabilityVerificationResult verify_capability_statement(
    const fl::worker::v1::SignedCapabilityStatement& statement, double now_unix_s) {
    const std::string canonical = canonical_capability_payload_json(statement);
    const std::string recomputed_hash = sha256_hex(canonical);
    if (recomputed_hash != statement.payload_hash()) {
        return {false, "payload_hash does not match the payload"};
    }

    bool public_key_ok = false;
    const auto public_key_bytes = hex_decode(statement.signing_public_key(), public_key_ok);
    bool signature_ok = false;
    const auto signature_bytes = hex_decode(statement.signature(), signature_ok);
    if (!public_key_ok || !signature_ok) {
        return {false, "signing_public_key or signature is not valid hex"};
    }
    if (!ed25519_verify(public_key_bytes, signature_bytes, canonical)) {
        return {false, "invalid signature"};
    }

    if (statement.expires_at() <= 0.0 || now_unix_s >= statement.expires_at()) {
        return {false, "capability statement has expired"};
    }

    return {true, "ok"};
}

}  // namespace fl::coordinator
