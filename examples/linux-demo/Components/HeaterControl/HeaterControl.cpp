#include "Components/HeaterControl/HeaterControl.hpp"

namespace Components {

HeaterControl::HeaterControl(const char* compName) : HeaterControlComponentBase(compName), m_trips(0) {}

Drv::GpioStatus HeaterControl::setHeater(Fw::On state) {
    const Drv::GpioStatus status =
        this->heaterWrite_out(0, (state == Fw::On::ON) ? Fw::Logic::HIGH : Fw::Logic::LOW);
    if (status == Drv::GpioStatus::OP_OK) {
        this->tlmWrite_HeaterState(state);
    } else {
        this->log_WARNING_HI_HeaterWriteError(status);
    }
    return status;
}

void HeaterControl::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    Fw::Logic level = Fw::Logic::LOW;
    if (this->switchRead_out(0, level) == Drv::GpioStatus::OP_OK) {
        this->tlmWrite_SwitchState(level);
    }
}

void HeaterControl::switchTripped_handler(FwIndexType portNum, Os::RawTime& cycleStart) {
    (void)portNum;
    (void)cycleStart;
    m_trips++;
    this->tlmWrite_TripCount(m_trips);
    (void)this->setHeater(Fw::On::OFF);
    this->log_WARNING_HI_OverTemperature();
}

void HeaterControl::HEATER_SET_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::On& state) {
    const Drv::GpioStatus status = this->setHeater(state);
    if (status != Drv::GpioStatus::OP_OK) {
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return;
    }
    this->log_ACTIVITY_HI_HeaterSet(state);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Components
