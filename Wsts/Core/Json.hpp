// ======================================================================
// \title  Json.hpp
// \brief  Minimal flat JSON object used by WSTS actions, scenarios, recordings and the bridge
//
// Only flat objects are supported: {"key": "string" | number | true | false | null, ...}.
// This is deliberately small: the WSTS wire/file formats are line-delimited flat objects.
// ======================================================================
#ifndef WSTS_CORE_JSON_HPP
#define WSTS_CORE_JSON_HPP

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace Wsts {

using Bytes = std::vector<std::uint8_t>;

//! Flat JSON object. Values are stored as text plus a flag saying whether they were quoted.
class JsonObj {
  public:
    //! Parse one line. Returns false and fills `error` on malformed or nested input.
    bool parse(const std::string& line, std::string& error);

    //! Serialize to a single line (keys in sorted order, so output is stable)
    std::string dump() const;

    bool has(const std::string& key) const;
    std::string str(const std::string& key, const std::string& fallback = "") const;
    //! Numeric value; accepts decimal and 0x-prefixed hex, in quoted or unquoted form
    std::int64_t num(const std::string& key, std::int64_t fallback = 0) const;
    //! Unsigned 64-bit value (register offsets and values); the same forms as num()
    std::uint64_t unum(const std::string& key, std::uint64_t fallback = 0) const;
    double real(const std::string& key, double fallback = 0.0) const;
    bool flag(const std::string& key, bool fallback = false) const;

    JsonObj& set(const std::string& key, const std::string& value);
    JsonObj& set(const std::string& key, const char* value);
    JsonObj& setNum(const std::string& key, std::int64_t value);
    //! A 64-bit value as a quoted "0x..." string: exact in every JSON reader, and read back
    //! by unum() and num()
    JsonObj& setHex(const std::string& key, std::uint64_t value);
    JsonObj& setReal(const std::string& key, double value);
    JsonObj& setBool(const std::string& key, bool value);

  private:
    struct Value {
        std::string text;
        bool quoted;
    };
    std::map<std::string, Value> m_values;
};

//! Lowercase hex encoding of a byte vector
std::string toHex(const Bytes& data);
//! Decode hex (whitespace and optional 0x prefix ignored). Returns false on bad input.
bool fromHex(const std::string& hex, Bytes& out);
//! Parse an integer in decimal or 0x hex. Returns false on bad input.
bool parseInt(const std::string& text, std::int64_t& out);
//! Parse an unsigned 64-bit integer in decimal or 0x hex. Returns false on bad input.
bool parseUInt(const std::string& text, std::uint64_t& out);

}  // namespace Wsts

#endif
