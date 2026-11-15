#include "SensorSim/SimTime/SimTime.hpp"

#include <Wsts/Core/Control.hpp>

namespace SensorSim {

SimTime::SimTime(const char* compName) : SimTimeComponentBase(compName) {}

void SimTime::timeGetPort_handler(FwIndexType portNum, Fw::Time& time) {
    (void)portNum;
    // The workstation's clock in real time, as the flight component gives on Linux, and
    // simulated time in stepped time: reading the host's clock here would make two stepped
    // runs differ (the 'Time' section of the report lists code that does)
    std::uint32_t seconds = 0;
    std::uint32_t microseconds = 0;
    Wsts::Control::get().flightTime(seconds, microseconds);
    time.set(TimeBase::TB_WORKSTATION_TIME, seconds, microseconds);
}

}  // namespace SensorSim
