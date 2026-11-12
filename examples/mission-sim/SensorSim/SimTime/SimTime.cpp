#include "SensorSim/SimTime/SimTime.hpp"

#include <chrono>

namespace SensorSim {

SimTime::SimTime(const char* compName) : SimTimeComponentBase(compName) {}

void SimTime::timeGetPort_handler(FwIndexType portNum, Fw::Time& time) {
    (void)portNum;
    // The workstation's clock, as the flight component gives on Linux
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    time.set(TimeBase::TB_WORKSTATION_TIME,
             static_cast<U32>(std::chrono::duration_cast<std::chrono::seconds>(now).count()),
             static_cast<U32>(std::chrono::duration_cast<std::chrono::microseconds>(now).count() % 1000000));
}

}  // namespace SensorSim
