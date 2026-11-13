// Fixed conversions for unit tests of the components that use them
#include "../include/units/convert.h"

namespace Units {

std::uint32_t millisToTicks(std::uint32_t milliseconds, std::uint32_t hz) {
    (void)milliseconds;
    (void)hz;
    return 1;
}

double countsToCelsius(std::uint16_t counts) {
    (void)counts;
    return 20.0;
}

}  // namespace Units
