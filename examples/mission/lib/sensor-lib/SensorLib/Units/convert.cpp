// The flight toolchain's <cstdint> brings in <cstring>, so nobody noticed that this file
// uses memcpy without including it. A host compiler's standard library need not.
#include "units/convert.h"

namespace Units {

std::uint32_t millisToTicks(std::uint32_t milliseconds, std::uint32_t hz) {
    const std::uint32_t ticks = static_cast<std::uint32_t>((static_cast<std::uint64_t>(milliseconds) * hz) / 1000u);
    return (ticks == 0) ? 1 : ticks;
}

std::uint16_t reading16(const std::uint8_t* wire) {
    std::uint8_t bytes[2];
    memcpy(bytes, wire, sizeof bytes);
    return static_cast<std::uint16_t>(bytes[0] | (bytes[1] << 8));
}

double countsToCelsius(std::uint16_t counts) {
    return (static_cast<double>(counts) * 3.3 / 1023.0 - 0.5) * 100.0;
}

}  // namespace Units
