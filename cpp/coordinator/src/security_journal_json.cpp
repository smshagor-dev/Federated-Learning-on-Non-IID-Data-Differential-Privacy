#include "fl_coordinator/security_journal_json.hpp"

#include <stdexcept>

namespace fl::coordinator {

namespace {

class Cursor {
  public:
    explicit Cursor(const std::string& text) : text_(text) {}

    void skip_ws() {
        while (pos_ < text_.size() &&
               (text_[pos_] == ' ' || text_[pos_] == '\t' || text_[pos_] == '\n' ||
                text_[pos_] == '\r')) {
            ++pos_;
        }
    }

    bool at_end() const { return pos_ >= text_.size(); }
    char peek() const { return at_end() ? '\0' : text_[pos_]; }
    bool consume(char expected) {
        if (peek() != expected) {
            return false;
        }
        ++pos_;
        return true;
    }

    // Parses a JSON string literal (the opening quote must be the
    // current character); returns std::nullopt on any malformed escape
    // or unterminated string.
    std::optional<std::string> parse_string() {
        if (!consume('"')) {
            return std::nullopt;
        }
        std::string out;
        while (true) {
            if (at_end()) {
                return std::nullopt;
            }
            const char c = text_[pos_++];
            if (c == '"') {
                return out;
            }
            if (c != '\\') {
                out += c;
                continue;
            }
            if (at_end()) {
                return std::nullopt;
            }
            const char escape = text_[pos_++];
            switch (escape) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    if (pos_ + 4 > text_.size()) {
                        return std::nullopt;
                    }
                    unsigned int code_unit = 0;
                    for (int k = 0; k < 4; ++k) {
                        const char hex = text_[pos_++];
                        int digit;
                        if (hex >= '0' && hex <= '9') digit = hex - '0';
                        else if (hex >= 'a' && hex <= 'f') digit = hex - 'a' + 10;
                        else if (hex >= 'A' && hex <= 'F') digit = hex - 'A' + 10;
                        else return std::nullopt;
                        code_unit = (code_unit << 4) | static_cast<unsigned int>(digit);
                    }
                    // This module's own encoder only ever emits \u00XX for
                    // single raw bytes (0x80-0xFF) or standard control
                    // characters -- never a real surrogate pair. Decode
                    // conservatively: values <= 0xFF map back to the
                    // original single byte; anything else is re-emitted as
                    // a 3-byte UTF-8 sequence so a foreign/corrupt line
                    // still produces *some* deterministic bytes rather than
                    // crashing.
                    if (code_unit <= 0xFF) {
                        out += static_cast<char>(code_unit);
                    } else {
                        out += static_cast<char>(0xE0 | (code_unit >> 12));
                        out += static_cast<char>(0x80 | ((code_unit >> 6) & 0x3F));
                        out += static_cast<char>(0x80 | (code_unit & 0x3F));
                    }
                    break;
                }
                default:
                    return std::nullopt;
            }
        }
    }

    // Parses a JSON number/bool/null literal as raw text (digits, '-',
    // '.', 'e'/'E', or the literal words true/false/null), stopping at
    // the first ',' '}' ']' or whitespace.
    std::string parse_bare_literal() {
        const std::size_t start = pos_;
        while (!at_end() && text_[pos_] != ',' && text_[pos_] != '}' && text_[pos_] != ']' &&
               text_[pos_] != ' ' && text_[pos_] != '\t' && text_[pos_] != '\n' &&
               text_[pos_] != '\r') {
            ++pos_;
        }
        return text_.substr(start, pos_ - start);
    }

  private:
    const std::string& text_;
    std::size_t pos_ = 0;
};

// Parses a nested object whose values must all be JSON strings (this is
// the shape safe_details/details always take). Returns std::nullopt on
// any structural mismatch.
std::optional<std::map<std::string, std::string>> parse_string_map(Cursor& cursor) {
    if (!cursor.consume('{')) {
        return std::nullopt;
    }
    std::map<std::string, std::string> result;
    cursor.skip_ws();
    if (cursor.consume('}')) {
        return result;
    }
    while (true) {
        cursor.skip_ws();
        const auto key = cursor.parse_string();
        if (!key) {
            return std::nullopt;
        }
        cursor.skip_ws();
        if (!cursor.consume(':')) {
            return std::nullopt;
        }
        cursor.skip_ws();
        const auto value = cursor.parse_string();
        if (!value) {
            return std::nullopt;
        }
        result[*key] = *value;
        cursor.skip_ws();
        if (cursor.consume(',')) {
            continue;
        }
        if (cursor.consume('}')) {
            return result;
        }
        return std::nullopt;
    }
}

}  // namespace

std::optional<JsonFlatObject> parse_shallow_json_object(const std::string& text) {
    Cursor cursor(text);
    cursor.skip_ws();
    if (!cursor.consume('{')) {
        return std::nullopt;
    }
    JsonFlatObject result;
    cursor.skip_ws();
    if (cursor.consume('}')) {
        return result;
    }
    while (true) {
        cursor.skip_ws();
        const auto key = cursor.parse_string();
        if (!key) {
            return std::nullopt;
        }
        cursor.skip_ws();
        if (!cursor.consume(':')) {
            return std::nullopt;
        }
        cursor.skip_ws();
        JsonScalarOrMap value;
        if (cursor.peek() == '{') {
            const auto nested = parse_string_map(cursor);
            if (!nested) {
                return std::nullopt;
            }
            value.is_map = true;
            value.nested = *nested;
        } else if (cursor.peek() == '"') {
            const auto scalar = cursor.parse_string();
            if (!scalar) {
                return std::nullopt;
            }
            value.scalar = *scalar;
        } else {
            value.scalar = cursor.parse_bare_literal();
            if (value.scalar.empty()) {
                return std::nullopt;
            }
        }
        result[*key] = value;
        cursor.skip_ws();
        if (cursor.consume(',')) {
            continue;
        }
        if (cursor.consume('}')) {
            return result;
        }
        return std::nullopt;
    }
}

std::string json_field_string(const JsonFlatObject& object, const std::string& key) {
    const auto it = object.find(key);
    if (it == object.end() || it->second.is_map) {
        return "";
    }
    return it->second.scalar;
}

std::uint64_t json_field_uint(const JsonFlatObject& object, const std::string& key) {
    const std::string raw = json_field_string(object, key);
    if (raw.empty()) {
        return 0;
    }
    try {
        return std::stoull(raw);
    } catch (const std::exception&) {
        return 0;
    }
}

int json_field_int(const JsonFlatObject& object, const std::string& key) {
    const std::string raw = json_field_string(object, key);
    if (raw.empty()) {
        return 0;
    }
    try {
        return std::stoi(raw);
    } catch (const std::exception&) {
        return 0;
    }
}

std::map<std::string, std::string> json_field_map(const JsonFlatObject& object,
                                                    const std::string& key) {
    const auto it = object.find(key);
    if (it == object.end() || !it->second.is_map) {
        return {};
    }
    return it->second.nested;
}

}  // namespace fl::coordinator
