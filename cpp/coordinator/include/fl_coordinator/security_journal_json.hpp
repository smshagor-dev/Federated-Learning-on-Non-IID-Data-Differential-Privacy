#pragma once

// A minimal, strict JSON object parser scoped to exactly the shape the
// SecurityEvent/SecurityAuditRecord canonical encoders produce: a flat
// top-level object whose values are strings, integers, booleans, or (at
// most one level deep) a nested object of string -> string. This is
// deliberately NOT a general-purpose JSON library -- this codebase has
// none, by design (see docs/canonical-security-serialization.md's "What
// this is not" section for the same rationale applied to the encoder
// side). It exists only so SecurityEventJournal/SecurityAuditJournal can
// reload their own previously-written JSONL lines; it is never used on
// untrusted network input.
//
// Parsing failure (malformed JSON, unexpected structure) returns
// std::nullopt rather than throwing -- a caller reloading a journal
// treats an unparseable line as a corrupt record to skip, not a fatal
// error (see security_event_journal.hpp's corruption-recovery policy).

#include <cstdint>
#include <map>
#include <optional>
#include <string>

namespace fl::coordinator {

struct JsonScalarOrMap {
    bool is_map = false;
    std::string scalar;                          // valid when !is_map
    std::map<std::string, std::string> nested;    // valid when is_map (string -> string only)
};

using JsonFlatObject = std::map<std::string, JsonScalarOrMap>;

// Parses `text` (expected to be exactly one JSON object, no trailing
// content besides whitespace) into a flat map of field name -> value.
[[nodiscard]] std::optional<JsonFlatObject> parse_shallow_json_object(const std::string& text);

// Convenience accessors used by journal reload code; return "" / 0 / an
// empty map when the field is absent or of the wrong shape rather than
// throwing.
[[nodiscard]] std::string json_field_string(const JsonFlatObject& object, const std::string& key);
[[nodiscard]] std::uint64_t json_field_uint(const JsonFlatObject& object, const std::string& key);
[[nodiscard]] int json_field_int(const JsonFlatObject& object, const std::string& key);
[[nodiscard]] std::map<std::string, std::string> json_field_map(const JsonFlatObject& object,
                                                                  const std::string& key);

}  // namespace fl::coordinator
