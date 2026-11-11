// ======================================================================
// \title  Json.cpp
// \brief  Minimal flat JSON object implementation
// ======================================================================
#include <Sse/Core/Json.hpp>

#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstdlib>

namespace Sse {

namespace {

void skipSpace(const std::string& s, std::size_t& i) {
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) {
        i++;
    }
}

bool parseString(const std::string& s, std::size_t& i, std::string& out, std::string& error) {
    if (i >= s.size() || s[i] != '"') {
        error = "expected '\"'";
        return false;
    }
    i++;
    out.clear();
    while (i < s.size()) {
        const char c = s[i++];
        if (c == '"') {
            return true;
        }
        if (c != '\\') {
            out.push_back(c);
            continue;
        }
        if (i >= s.size()) {
            break;
        }
        const char e = s[i++];
        switch (e) {
            case 'n':
                out.push_back('\n');
                break;
            case 'r':
                out.push_back('\r');
                break;
            case 't':
                out.push_back('\t');
                break;
            case 'b':
                out.push_back('\b');
                break;
            case 'f':
                out.push_back('\f');
                break;
            case 'u': {
                if (i + 4 > s.size()) {
                    error = "bad \\u escape";
                    return false;
                }
                const unsigned long code = std::strtoul(s.substr(i, 4).c_str(), nullptr, 16);
                i += 4;
                // Only the single-byte range is meaningful for SSE payload text
                out.push_back(static_cast<char>(code & 0xFF));
                break;
            }
            default:
                out.push_back(e);
                break;
        }
    }
    error = "unterminated string";
    return false;
}

std::string quote(const std::string& in) {
    std::string out = "\"";
    for (const char c : in) {
        switch (c) {
            case '"':
                out += "\\\"";
                break;
            case '\\':
                out += "\\\\";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", static_cast<unsigned>(static_cast<unsigned char>(c)));
                    out += buf;
                } else {
                    out.push_back(c);
                }
                break;
        }
    }
    out.push_back('"');
    return out;
}

}  // namespace

bool JsonObj::parse(const std::string& line, std::string& error) {
    m_values.clear();
    std::size_t i = 0;
    skipSpace(line, i);
    if (i >= line.size() || line[i] != '{') {
        error = "expected '{'";
        return false;
    }
    i++;
    skipSpace(line, i);
    if (i < line.size() && line[i] == '}') {
        return true;
    }
    while (true) {
        skipSpace(line, i);
        std::string key;
        if (!parseString(line, i, key, error)) {
            return false;
        }
        skipSpace(line, i);
        if (i >= line.size() || line[i] != ':') {
            error = "expected ':' after key '" + key + "'";
            return false;
        }
        i++;
        skipSpace(line, i);
        if (i >= line.size()) {
            error = "missing value for key '" + key + "'";
            return false;
        }
        Value value{"", false};
        if (line[i] == '"') {
            value.quoted = true;
            if (!parseString(line, i, value.text, error)) {
                return false;
            }
        } else if (line[i] == '{' || line[i] == '[') {
            error = "nested values are not supported (key '" + key + "')";
            return false;
        } else {
            const std::size_t start = i;
            while (i < line.size() && line[i] != ',' && line[i] != '}' &&
                   !std::isspace(static_cast<unsigned char>(line[i]))) {
                i++;
            }
            value.text = line.substr(start, i - start);
        }
        m_values[key] = value;
        skipSpace(line, i);
        if (i < line.size() && line[i] == ',') {
            i++;
            continue;
        }
        if (i < line.size() && line[i] == '}') {
            return true;
        }
        error = "expected ',' or '}'";
        return false;
    }
}

std::string JsonObj::dump() const {
    std::string out = "{";
    bool first = true;
    for (const auto& entry : m_values) {
        if (!first) {
            out += ",";
        }
        first = false;
        out += quote(entry.first) + ":";
        out += entry.second.quoted ? quote(entry.second.text) : entry.second.text;
    }
    out += "}";
    return out;
}

bool JsonObj::has(const std::string& key) const {
    return m_values.find(key) != m_values.end();
}

std::string JsonObj::str(const std::string& key, const std::string& fallback) const {
    const auto it = m_values.find(key);
    return (it == m_values.end()) ? fallback : it->second.text;
}

std::int64_t JsonObj::num(const std::string& key, std::int64_t fallback) const {
    const auto it = m_values.find(key);
    if (it == m_values.end()) {
        return fallback;
    }
    if (it->second.text == "true") {
        return 1;
    }
    if (it->second.text == "false") {
        return 0;
    }
    std::int64_t value = 0;
    if (parseInt(it->second.text, value)) {
        return value;
    }
    // Tolerate values such as 2.0
    char* end = nullptr;
    const double real = std::strtod(it->second.text.c_str(), &end);
    return (end != it->second.text.c_str()) ? static_cast<std::int64_t>(real) : fallback;
}

double JsonObj::real(const std::string& key, double fallback) const {
    const auto it = m_values.find(key);
    if (it == m_values.end()) {
        return fallback;
    }
    char* end = nullptr;
    const double value = std::strtod(it->second.text.c_str(), &end);
    return (end != it->second.text.c_str()) ? value : fallback;
}

bool JsonObj::flag(const std::string& key, bool fallback) const {
    const auto it = m_values.find(key);
    if (it == m_values.end()) {
        return fallback;
    }
    const std::string& t = it->second.text;
    if (t == "true" || t == "1" || t == "high" || t == "HIGH") {
        return true;
    }
    if (t == "false" || t == "0" || t == "low" || t == "LOW") {
        return false;
    }
    return fallback;
}

JsonObj& JsonObj::set(const std::string& key, const std::string& value) {
    m_values[key] = Value{value, true};
    return *this;
}

JsonObj& JsonObj::set(const std::string& key, const char* value) {
    return this->set(key, std::string(value));
}

JsonObj& JsonObj::setNum(const std::string& key, std::int64_t value) {
    m_values[key] = Value{std::to_string(value), false};
    return *this;
}

JsonObj& JsonObj::setReal(const std::string& key, double value) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.9g", value);
    m_values[key] = Value{buf, false};
    return *this;
}

JsonObj& JsonObj::setBool(const std::string& key, bool value) {
    m_values[key] = Value{value ? "true" : "false", false};
    return *this;
}

std::string toHex(const Bytes& data) {
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(data.size() * 2);
    for (const std::uint8_t b : data) {
        out.push_back(digits[b >> 4]);
        out.push_back(digits[b & 0xF]);
    }
    return out;
}

bool fromHex(const std::string& hex, Bytes& out) {
    out.clear();
    std::string clean;
    for (const char c : hex) {
        if (!std::isspace(static_cast<unsigned char>(c))) {
            clean.push_back(c);
        }
    }
    if (clean.size() >= 2 && clean[0] == '0' && (clean[1] == 'x' || clean[1] == 'X')) {
        clean = clean.substr(2);
    }
    if (clean.size() % 2 != 0) {
        return false;
    }
    for (std::size_t i = 0; i < clean.size(); i += 2) {
        if (!std::isxdigit(static_cast<unsigned char>(clean[i])) ||
            !std::isxdigit(static_cast<unsigned char>(clean[i + 1]))) {
            return false;
        }
        out.push_back(static_cast<std::uint8_t>(std::strtoul(clean.substr(i, 2).c_str(), nullptr, 16)));
    }
    return true;
}

bool parseInt(const std::string& text, std::int64_t& out) {
    if (text.empty()) {
        return false;
    }
    errno = 0;
    char* end = nullptr;
    const long long value = std::strtoll(text.c_str(), &end, 0);
    if (errno != 0 || end == text.c_str() || *end != '\0') {
        return false;
    }
    out = value;
    return true;
}

}  // namespace Sse
