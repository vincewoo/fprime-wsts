// ======================================================================
// \title  SimTime.cpp
// \brief  Time source of a simulated deployment: the host's clock, or simulated time
// ======================================================================
#include <Wsts/Svc/SimTime/SimTime.hpp>

#include <Wsts/Core/Control.hpp>

namespace Wsts {

SimTime::SimTime(const char* compName) : SimTimeComponentBase(compName) {}

void SimTime::timeGetPort_handler(FwIndexType portNum, Fw::Time& time) {
    (void)portNum;
    // The same time base either way: ground tools take both for a workstation's clock
    std::uint32_t seconds = 0;
    std::uint32_t microseconds = 0;
    Control::get().flightTime(seconds, microseconds);
    time.set(TimeBase::TB_WORKSTATION_TIME, seconds, microseconds);
}

}  // namespace Wsts
