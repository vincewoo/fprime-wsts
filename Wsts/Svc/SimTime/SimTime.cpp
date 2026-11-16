// ======================================================================
// \title  SimTime.cpp
// \brief  Time source of a simulated deployment: the host's clock, or simulated time
// ======================================================================
#include <Wsts/Svc/SimTime/SimTime.hpp>

#include <Wsts/Core/Control.hpp>

namespace Wsts {

SimTime::SimTime(const char* compName)
    : SimTimeComponentBase(compName), m_epoch(Fw::ZERO_TIME) {}

void SimTime::timeGetPort_handler(FwIndexType portNum, Fw::Time& time) {
    (void)portNum;
    // The same time base either way: ground tools take both for a workstation's clock
    std::uint32_t seconds = 0;
    std::uint32_t microseconds = 0;
    Control::get().flightTime(seconds, microseconds);
    time.set(TimeBase::TB_WORKSTATION_TIME, seconds, microseconds);
}

void SimTime::set_epoch(const Fw::Time& fw_time, const Os::RawTime& os_time) {
    // Recorded for what asked for it; the time itself still comes from Control
    (void)os_time;
    m_epoch = fw_time;
}

void SimTime::setEpoch_handler(FwIndexType portNum, const Fw::Time& fw_time, const Os::RawTime& os_time) {
    (void)portNum;
    this->set_epoch(fw_time, os_time);
}

void SimTime::SetCurrentTime_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 seconds_now) {
    (void)opCode;
    (void)cmdSeq;
    (void)seconds_now;
    // Accepted: the simulation owns the time, and there is nothing to set
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Wsts
