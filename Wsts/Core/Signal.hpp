// ======================================================================
// \title  Signal.hpp
// \brief  Values that change with simulated time, for the parameters of device models
//
// A signal is a small expression of simulated time in seconds, written where a model takes
// a number:
//
//   sine(offset=25, amplitude=5, period=60) + noise(sd=0.1)
//   ramp(from=12.6, to=11.1, duration=600) * 1000
//   square(low=0, high=1, period=2) as u8
//
// Functions take numbers, by position (in the order below) or by name:
//
//   sine(offset=0, amplitude=1, period=1, phase=0)       offset + amplitude * sin(2π (t/period + phase))
//   square(low=0, high=1, period=1, duty=0.5, phase=0)   high for the first `duty` of each period
//   triangle(low=0, high=1, period=1, phase=0)           low → high → low over each period
//   sawtooth(low=0, high=1, period=1, phase=0)           low → high over each period, then back
//   ramp(from=0, to=1, start=0, duration=1)              from, then linearly to `to`, then holds
//   step(before=0, after=1, at=0)                        before until `at` seconds, then after
//   noise(sd=1, mean=0, seed=0)                          Gaussian, a new sample at each instant
//   uniform(low=0, high=1, seed=0)                       uniform, a new sample at each instant
//
// `t` is the simulated time in seconds. `+ - * /` and parentheses combine terms. Periods,
// phases (in periods), starts and durations are in seconds of simulated time
// (Control::elapsedNs(): tick count times tick period), so two runs in stepped time see the
// same values. Random samples are a hash of the seed and the instant, not a sequence: a
// value does not depend on how often it was read.
//
// A value written for a model's register adds how it is stored: `<expression> as <format>`.
// A format is u8, s8, u16, s16, u24, s24, u32 or s32, with `be` or `le` (big or little
// endian) for formats wider than a byte where a model stores bytes (u16be). Values are
// rounded to the nearest integer and held at the ends of the format's range.
//
// The same language, evaluated the same way, is in python/fprime_wsts/signals.py.
// ======================================================================
#ifndef WSTS_CORE_SIGNAL_HPP
#define WSTS_CORE_SIGNAL_HPP

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace Wsts {

class Signal {
  public:
    struct Node;

    Signal();
    ~Signal();
    Signal(const Signal& other);
    Signal& operator=(const Signal& other);

    //! Parse an expression. On failure returns false and says why in `error`.
    static bool parse(const std::string& text, Signal& signal, std::string& error);
    //! The value at `ns` nanoseconds of simulated time
    double at(std::uint64_t ns) const;
    //! True when the value does not depend on time
    bool constant() const;
    const std::string& text() const { return m_text; }

  private:
    std::shared_ptr<const Node> m_root;
    std::string m_text;
};

//! How a signal's value is stored in a register
struct SignalFormat {
    unsigned bytes = 1;     //!< 1 to 4
    bool isSigned = false;
    bool bigEndian = true;
    bool explicitOrder = false;  //!< `be` or `le` was given

    //! Parse "u16be", "s8", "u32", ... Returns false for anything else.
    static bool parse(const std::string& text, SignalFormat& format);
    //! Round and saturate to the format's range; two's complement in the low `bytes` bytes
    std::uint32_t encode(double value) const;
    //! The encoded value as bytes, in the format's order
    std::vector<std::uint8_t> toBytes(double value) const;
};

//! A signal and how it is stored: `<expression> [as <format>]`
struct StoredSignal {
    Signal signal;
    SignalFormat format;
    bool hasFormat = false;

    //! Parse `<expression> [as <format>]`. On failure returns false and says why in `error`.
    static bool parse(const std::string& text, StoredSignal& stored, std::string& error);
};

}  // namespace Wsts

#endif
