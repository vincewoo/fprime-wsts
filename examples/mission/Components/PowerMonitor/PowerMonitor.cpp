#include "Components/PowerMonitor/PowerMonitor.hpp"

namespace Components {

PowerMonitor::PowerMonitor(const char* compName) : PowerMonitorComponentBase(compName) {}

Drv::I2cStatus PowerMonitor::readRegister(U8 reg, U16& value) {
    U8 raw[2] = {0, 0};
    Fw::Buffer writeBuffer(&reg, 1);
    Fw::Buffer readBuffer(raw, sizeof raw);
    const Drv::I2cStatus status = this->i2cWriteRead_out(0, I2C_ADDRESS, writeBuffer, readBuffer);
    value = static_cast<U16>((raw[0] << 8) | raw[1]);
    return status;
}

void PowerMonitor::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    U16 voltage = 0;
    U16 current = 0;
    Drv::I2cStatus status = this->readRegister(REG_BUS_VOLTAGE, voltage);
    if (status == Drv::I2cStatus::I2C_OK) {
        status = this->readRegister(REG_CURRENT, current);
    }
    if (status != Drv::I2cStatus::I2C_OK) {
        this->log_WARNING_LO_PowerReadError(status);
        return;
    }
    this->tlmWrite_BusVoltage(static_cast<F32>((voltage >> 3) * MILLIVOLTS_PER_LSB) / 1000.0f);
    this->tlmWrite_Current(static_cast<I16>(current));
}

}  // namespace Components
