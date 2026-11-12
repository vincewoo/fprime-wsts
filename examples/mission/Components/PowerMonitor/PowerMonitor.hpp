#ifndef COMPONENTS_POWER_MONITOR_HPP
#define COMPONENTS_POWER_MONITOR_HPP

#include "Components/PowerMonitor/PowerMonitorComponentAc.hpp"

namespace Components {

class PowerMonitor final : public PowerMonitorComponentBase {
  public:
    static constexpr U32 I2C_ADDRESS = 0x40;
    static constexpr U8 REG_BUS_VOLTAGE = 0x02;
    static constexpr U8 REG_CURRENT = 0x04;
    static constexpr U32 MILLIVOLTS_PER_LSB = 4;  // bus voltage register, bits 15..3

    explicit PowerMonitor(const char* compName);
    ~PowerMonitor() = default;

  private:
    void run_handler(FwIndexType portNum, U32 context) override;

    //! Read the 16-bit big-endian register `reg`
    Drv::I2cStatus readRegister(U8 reg, U16& value);
};

}  // namespace Components

#endif
