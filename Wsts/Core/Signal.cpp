// ======================================================================
// \title  Signal.cpp
// \brief  Values that change with simulated time
// ======================================================================
#include <Wsts/Core/Signal.hpp>

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <limits>

namespace Wsts {

namespace {

constexpr double TWO_PI = 6.283185307179586476925286766559;

struct Function {
    const char* name;
    std::vector<std::pair<const char*, double>> params;  // in positional order, with defaults
};

// The order of this table is the order of Node::fn
const std::vector<Function>& functions() {
    static const std::vector<Function> table = {
        {"sine", {{"offset", 0}, {"amplitude", 1}, {"period", 1}, {"phase", 0}}},
        {"square", {{"low", 0}, {"high", 1}, {"period", 1}, {"duty", 0.5}, {"phase", 0}}},
        {"triangle", {{"low", 0}, {"high", 1}, {"period", 1}, {"phase", 0}}},
        {"sawtooth", {{"low", 0}, {"high", 1}, {"period", 1}, {"phase", 0}}},
        {"ramp", {{"from", 0}, {"to", 1}, {"start", 0}, {"duration", 1}}},
        {"step", {{"before", 0}, {"after", 1}, {"at", 0}}},
        {"noise", {{"sd", 1}, {"mean", 0}, {"seed", 0}}},
        {"uniform", {{"low", 0}, {"high", 1}, {"seed", 0}}},
    };
    return table;
}

enum Fn { SINE, SQUARE, TRIANGLE, SAWTOOTH, RAMP, STEP, NOISE, UNIFORM };

std::uint64_t splitmix64(std::uint64_t x) {
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}

//! Uniform in (0, 1], from 53 bits of a hash
double unit(std::uint64_t hash) {
    return (static_cast<double>(hash >> 11) + 1.0) * (1.0 / 9007199254740992.0);
}

double fraction(double x) {
    return x - std::floor(x);
}

}  // namespace

struct Signal::Node {
    enum Kind { NUMBER, TIME, NEGATE, ADD, SUBTRACT, MULTIPLY, DIVIDE, FUNCTION } kind = NUMBER;
    double number = 0;
    int fn = 0;
    std::vector<double> args;
    std::shared_ptr<const Node> left;
    std::shared_ptr<const Node> right;

    double eval(std::uint64_t ns) const {
        const double t = static_cast<double>(ns) * 1e-9;
        switch (kind) {
            case NUMBER:
                return number;
            case TIME:
                return t;
            case NEGATE:
                return -left->eval(ns);
            case ADD:
                return left->eval(ns) + right->eval(ns);
            case SUBTRACT:
                return left->eval(ns) - right->eval(ns);
            case MULTIPLY:
                return left->eval(ns) * right->eval(ns);
            case DIVIDE: {
                const double divisor = right->eval(ns);
                return (divisor == 0) ? 0 : left->eval(ns) / divisor;
            }
            case FUNCTION:
                return this->function(t, ns);
        }
        return 0;
    }

    double function(double t, std::uint64_t ns) const {
        const std::vector<double>& a = args;
        switch (fn) {
            case SINE:
                return a[0] + a[1] * std::sin(TWO_PI * (t / a[2] + a[3]));
            case SQUARE:
                return (fraction(t / a[2] + a[4]) < a[3]) ? a[1] : a[0];
            case TRIANGLE: {
                const double x = fraction(t / a[2] + a[3]);
                return a[0] + (a[1] - a[0]) * ((x < 0.5) ? 2 * x : 2 * (1 - x));
            }
            case SAWTOOTH:
                return a[0] + (a[1] - a[0]) * fraction(t / a[2] + a[3]);
            case RAMP:
                if (t <= a[2]) {
                    return a[0];
                }
                if (a[3] <= 0 || t >= a[2] + a[3]) {
                    return a[1];
                }
                return a[0] + (a[1] - a[0]) * (t - a[2]) / a[3];
            case STEP:
                return (t < a[2]) ? a[0] : a[1];
            case NOISE: {
                const std::uint64_t h1 = splitmix64(static_cast<std::uint64_t>(std::llround(a[2])) ^ splitmix64(ns));
                const std::uint64_t h2 = splitmix64(h1);
                return a[1] + a[0] * std::sqrt(-2.0 * std::log(unit(h1))) * std::cos(TWO_PI * unit(h2));
            }
            case UNIFORM: {
                const std::uint64_t h = splitmix64(static_cast<std::uint64_t>(std::llround(a[2])) ^ splitmix64(ns));
                return a[0] + (a[1] - a[0]) * (unit(h) - (1.0 / 9007199254740992.0));
            }
            default:
                return 0;
        }
    }

    bool constant() const {
        switch (kind) {
            case NUMBER:
                return true;
            case TIME:
            case FUNCTION:
                return false;
            case NEGATE:
                return left->constant();
            default:
                return left->constant() && right->constant();
        }
    }
};

namespace {

using NodePtr = std::shared_ptr<Signal::Node>;

class Parser {
  public:
    explicit Parser(const std::string& text) : m_text(text) {}

    NodePtr parse(std::string& error) {
        NodePtr root = this->expression();
        this->skip();
        if (root && m_pos < m_text.size()) {
            this->fail("unexpected '" + m_text.substr(m_pos, 1) + "'");
        }
        if (!m_error.empty()) {
            error = m_error;
            return nullptr;
        }
        return root;
    }

  private:
    void skip() {
        while (m_pos < m_text.size() && std::isspace(static_cast<unsigned char>(m_text[m_pos]))) {
            m_pos++;
        }
    }

    bool accept(char c) {
        this->skip();
        if (m_pos < m_text.size() && m_text[m_pos] == c) {
            m_pos++;
            return true;
        }
        return false;
    }

    NodePtr fail(const std::string& message) {
        if (m_error.empty()) {
            m_error = message + " at position " + std::to_string(m_pos + 1) + " of '" + m_text + "'";
        }
        return nullptr;
    }

    static NodePtr binary(Signal::Node::Kind kind, NodePtr left, NodePtr right) {
        auto node = std::make_shared<Signal::Node>();
        node->kind = kind;
        node->left = std::move(left);
        node->right = std::move(right);
        return node;
    }

    NodePtr expression() {
        NodePtr left = this->term();
        while (left) {
            if (this->accept('+')) {
                NodePtr right = this->term();
                left = right ? binary(Signal::Node::ADD, left, right) : nullptr;
            } else if (this->accept('-')) {
                NodePtr right = this->term();
                left = right ? binary(Signal::Node::SUBTRACT, left, right) : nullptr;
            } else {
                break;
            }
        }
        return left;
    }

    NodePtr term() {
        NodePtr left = this->factor();
        while (left) {
            if (this->accept('*')) {
                NodePtr right = this->factor();
                left = right ? binary(Signal::Node::MULTIPLY, left, right) : nullptr;
            } else if (this->accept('/')) {
                NodePtr right = this->factor();
                left = right ? binary(Signal::Node::DIVIDE, left, right) : nullptr;
            } else {
                break;
            }
        }
        return left;
    }

    NodePtr factor() {
        this->skip();
        if (m_pos >= m_text.size()) {
            return this->fail("expected a number, t or a function");
        }
        if (this->accept('-')) {
            NodePtr inner = this->factor();
            if (!inner) {
                return nullptr;
            }
            auto node = std::make_shared<Signal::Node>();
            node->kind = Signal::Node::NEGATE;
            node->left = inner;
            return node;
        }
        if (this->accept('+')) {
            return this->factor();
        }
        if (this->accept('(')) {
            NodePtr inner = this->expression();
            if (inner && !this->accept(')')) {
                return this->fail("expected ')'");
            }
            return inner;
        }
        const char c = m_text[m_pos];
        if (std::isdigit(static_cast<unsigned char>(c)) || c == '.') {
            return this->number();
        }
        if (std::isalpha(static_cast<unsigned char>(c)) || c == '_') {
            const std::size_t start = m_pos;
            const std::string name = this->identifier();
            if (name == "t") {
                auto node = std::make_shared<Signal::Node>();
                node->kind = Signal::Node::TIME;
                return node;
            }
            return this->call(name, start);
        }
        return this->fail("unexpected '" + std::string(1, c) + "'");
    }

    NodePtr number() {
        const char* start = m_text.c_str() + m_pos;
        char* end = nullptr;
        double value = 0;
        if (m_text.compare(m_pos, 2, "0x") == 0 || m_text.compare(m_pos, 2, "0X") == 0) {
            value = static_cast<double>(std::strtoull(start, &end, 16));
        } else {
            value = std::strtod(start, &end);
        }
        if (end == start) {
            return this->fail("expected a number");
        }
        m_pos += static_cast<std::size_t>(end - start);
        auto node = std::make_shared<Signal::Node>();
        node->number = value;
        return node;
    }

    std::string identifier() {
        const std::size_t start = m_pos;
        while (m_pos < m_text.size() &&
               (std::isalnum(static_cast<unsigned char>(m_text[m_pos])) || m_text[m_pos] == '_')) {
            m_pos++;
        }
        return m_text.substr(start, m_pos - start);
    }

    NodePtr call(const std::string& name, std::size_t start) {
        const std::vector<Function>& table = functions();
        std::size_t fn = 0;
        while (fn < table.size() && name != table[fn].name) {
            fn++;
        }
        if (fn == table.size()) {
            std::string known;
            for (const Function& f : table) {
                known += (known.empty() ? "" : ", ") + std::string(f.name);
            }
            m_pos = start;
            return this->fail("unknown function '" + name + "' (known: " + known + ", and t)");
        }
        const Function& function = table[fn];
        auto node = std::make_shared<Signal::Node>();
        node->kind = Signal::Node::FUNCTION;
        node->fn = static_cast<int>(fn);
        for (const auto& param : function.params) {
            node->args.push_back(param.second);
        }
        std::vector<bool> given(function.params.size(), false);
        if (!this->accept('(')) {
            return this->fail("expected '(' after " + name);
        }
        std::size_t position = 0;
        bool named = false;
        if (!this->accept(')')) {
            do {
                this->skip();
                // A named argument: identifier followed by '='
                std::size_t index = position;
                const std::size_t mark = m_pos;
                if (m_pos < m_text.size() && std::isalpha(static_cast<unsigned char>(m_text[m_pos]))) {
                    const std::string key = this->identifier();
                    if (this->accept('=')) {
                        index = 0;
                        while (index < function.params.size() && key != function.params[index].first) {
                            index++;
                        }
                        if (index == function.params.size()) {
                            std::string known;
                            for (const auto& param : function.params) {
                                known += (known.empty() ? "" : ", ") + std::string(param.first);
                            }
                            return this->fail(name + "() has no argument '" + key + "' (it takes " + known + ")");
                        }
                        named = true;
                    } else {
                        m_pos = mark;
                    }
                }
                if (index == position) {
                    if (named && m_pos == mark) {
                        return this->fail("a positional argument of " + name + "() after a named one");
                    }
                    if (position >= function.params.size()) {
                        return this->fail(name + "() takes at most " + std::to_string(function.params.size()) +
                                          " arguments");
                    }
                    position++;
                }
                if (given[index]) {
                    return this->fail(name + "() is given '" + function.params[index].first + "' twice");
                }
                NodePtr arg = this->expression();
                if (!arg) {
                    return nullptr;
                }
                if (!arg->constant()) {
                    return this->fail("the arguments of " + name + "() must not depend on t");
                }
                node->args[index] = arg->eval(0);
                given[index] = true;
            } while (this->accept(','));
            if (!this->accept(')')) {
                return this->fail("expected ')' or ','");
            }
        }
        if ((fn == SINE || fn == TRIANGLE || fn == SAWTOOTH) && node->args[2] <= 0) {
            return this->fail(name + "(): period must be greater than 0");
        }
        if (fn == SQUARE && node->args[2] <= 0) {
            return this->fail(name + "(): period must be greater than 0");
        }
        if (fn == SQUARE && (node->args[3] < 0 || node->args[3] > 1)) {
            return this->fail(name + "(): duty must be between 0 and 1");
        }
        if (fn == RAMP && node->args[3] < 0) {
            return this->fail(name + "(): duration must not be negative");
        }
        return node;
    }

    const std::string& m_text;
    std::size_t m_pos = 0;
    std::string m_error;
};

std::string trim(const std::string& text) {
    std::size_t start = 0;
    std::size_t end = text.size();
    while (start < end && std::isspace(static_cast<unsigned char>(text[start]))) {
        start++;
    }
    while (end > start && std::isspace(static_cast<unsigned char>(text[end - 1]))) {
        end--;
    }
    return text.substr(start, end - start);
}

}  // namespace

Signal::Signal() = default;
Signal::~Signal() = default;
Signal::Signal(const Signal& other) = default;
Signal& Signal::operator=(const Signal& other) = default;

bool Signal::parse(const std::string& text, Signal& signal, std::string& error) {
    Parser parser(text);
    NodePtr root = parser.parse(error);
    if (!root) {
        return false;
    }
    signal.m_root = root;
    signal.m_text = trim(text);
    return true;
}

double Signal::at(std::uint64_t ns) const {
    return m_root ? m_root->eval(ns) : 0;
}

bool Signal::constant() const {
    return !m_root || m_root->constant();
}

bool SignalFormat::parse(const std::string& text, SignalFormat& format) {
    const std::string t = trim(text);
    if (t.size() < 2 || (t[0] != 'u' && t[0] != 's')) {
        return false;
    }
    SignalFormat parsed;
    parsed.isSigned = (t[0] == 's');
    std::size_t pos = 1;
    unsigned bits = 0;
    while (pos < t.size() && std::isdigit(static_cast<unsigned char>(t[pos]))) {
        bits = bits * 10 + static_cast<unsigned>(t[pos] - '0');
        pos++;
        if (bits > 64) {
            return false;
        }
    }
    if (bits != 8 && bits != 16 && bits != 24 && bits != 32) {
        return false;
    }
    parsed.bytes = bits / 8;
    const std::string order = t.substr(pos);
    if (order == "be" || order == "le") {
        parsed.bigEndian = (order == "be");
        parsed.explicitOrder = true;
    } else if (!order.empty()) {
        return false;
    }
    format = parsed;
    return true;
}

std::uint32_t SignalFormat::encode(double value) const {
    const unsigned bits = 8 * bytes;
    double low = 0;
    double high = 0;
    if (isSigned) {
        low = -std::ldexp(1.0, static_cast<int>(bits) - 1);
        high = std::ldexp(1.0, static_cast<int>(bits) - 1) - 1;
    } else {
        high = std::ldexp(1.0, static_cast<int>(bits)) - 1;
    }
    double rounded = std::isnan(value) ? 0 : std::round(value);
    rounded = (rounded < low) ? low : (rounded > high) ? high : rounded;
    const auto raw = static_cast<std::int64_t>(rounded);
    const std::uint64_t mask = (bits == 64) ? ~0ull : ((1ull << bits) - 1);
    return static_cast<std::uint32_t>(static_cast<std::uint64_t>(raw) & mask);
}

std::vector<std::uint8_t> SignalFormat::toBytes(double value) const {
    const std::uint32_t raw = this->encode(value);
    std::vector<std::uint8_t> out(bytes);
    for (unsigned i = 0; i < bytes; i++) {
        const unsigned shift = 8 * (bigEndian ? (bytes - 1 - i) : i);
        out[i] = static_cast<std::uint8_t>(raw >> shift);
    }
    return out;
}

bool StoredSignal::parse(const std::string& text, StoredSignal& stored, std::string& error) {
    StoredSignal parsed;
    std::string expression = text;
    // `as <format>` at the end
    const std::size_t as = text.rfind(" as ");
    if (as != std::string::npos) {
        const std::string format = trim(text.substr(as + 4));
        if (!SignalFormat::parse(format, parsed.format)) {
            error = "unknown format '" + format + "' (u8, s8, u16be, s16le, u24be, u32le, ...)";
            return false;
        }
        parsed.hasFormat = true;
        expression = text.substr(0, as);
    }
    if (!Signal::parse(expression, parsed.signal, error)) {
        return false;
    }
    stored = parsed;
    return true;
}

}  // namespace Wsts
