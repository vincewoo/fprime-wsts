#include "Components/ImuManager/ImuManager.hpp"

namespace Components {

ImuManager::ImuManager(const char* compName)
    : ImuManagerComponentBase(compName), m_detected(false), m_failing(false), m_errors(0), m_consecutiveErrors(0) {}

Drv::I2cStatus ImuManager::readRegisters(U8 reg, U8* data, U32 size) {
    Fw::Buffer writeBuffer(&reg, 1);
    Fw::Buffer readBuffer(data, size);
    return this->i2cWriteRead_out(0, I2C_ADDRESS, writeBuffer, readBuffer);
}

void ImuManager::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    Drv::I2cStatus status = Drv::I2cStatus::I2C_OK;
    if (!m_detected) {
        U8 whoAmI = 0;
        status = this->readRegisters(REG_WHO_AM_I, &whoAmI, 1);
        if (status == Drv::I2cStatus::I2C_OK) {
            m_detected = true;
            this->log_ACTIVITY_HI_ImuDetected(whoAmI);
        }
    }
    if (status == Drv::I2cStatus::I2C_OK) {
        U8 raw[6] = {0, 0, 0, 0, 0, 0};
        status = this->readRegisters(REG_ACCEL_XOUT_H, raw, sizeof raw);
        if (status == Drv::I2cStatus::I2C_OK) {
            this->tlmWrite_AccelX(static_cast<I16>((raw[0] << 8) | raw[1]));
            this->tlmWrite_AccelY(static_cast<I16>((raw[2] << 8) | raw[3]));
            this->tlmWrite_AccelZ(static_cast<I16>((raw[4] << 8) | raw[5]));
        }
    }
    if (status != Drv::I2cStatus::I2C_OK) {
        m_errors++;
        m_consecutiveErrors++;
        this->tlmWrite_ReadErrors(m_errors);
        // One event per outage, not one per failed sample
        if (!m_failing) {
            m_failing = true;
            this->log_WARNING_HI_ImuReadError(status);
        }
    } else if (m_failing) {
        m_failing = false;
        this->log_ACTIVITY_HI_ImuRecovered(m_consecutiveErrors);
        m_consecutiveErrors = 0;
    }
}

}  // namespace Components
