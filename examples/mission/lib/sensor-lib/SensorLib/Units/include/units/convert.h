// ======================================================================
// Unit conversions for sensor readings. Plain C++: no F Prime model.
//
// Users include this as <units/convert.h>: the SensorLib_Units target exports this
// directory to everything that depends on it.
// ======================================================================
#ifndef SENSOR_LIB_UNITS_CONVERT_H
#define SENSOR_LIB_UNITS_CONVERT_H

#include <cstdint>

namespace Units {

//! Milliseconds as ticks of a clock of the given rate, rounded down, at least one
std::uint32_t millisToTicks(std::uint32_t milliseconds, std::uint32_t hz);

//! A little-endian 16-bit reading taken from a sensor's wire format
std::uint16_t reading16(const std::uint8_t* wire);

//! Counts of a 10-bit converter as a temperature in degrees Celsius
double countsToCelsius(std::uint16_t counts);

}  // namespace Units

#endif
