#ifndef COMPONENTS_HEATER_CONTROL_HPP
#define COMPONENTS_HEATER_CONTROL_HPP

#include "Components/HeaterControl/HeaterControlComponentAc.hpp"

namespace Components {

class HeaterControl final : public HeaterControlComponentBase {
  public:
    explicit HeaterControl(const char* compName);
    ~HeaterControl() = default;

  private:
    void run_handler(FwIndexType portNum, U32 context) override;
    void switchTripped_handler(FwIndexType portNum, Os::RawTime& cycleStart) override;
    void HEATER_SET_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::On& state) override;

    Drv::GpioStatus setHeater(Fw::On state);

    U32 m_trips;
};

}  // namespace Components

#endif
