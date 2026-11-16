// ======================================================================
// \title  PowerSim.cpp
// \brief  Simulator of the power monitor that Components.PowerMonitor reads over I2C
// ======================================================================
#include <PowerSim/PowerSim.hpp>

namespace PowerSim {

PowerSim::PowerSim(const char* const compName)
    : PowerSimComponentBase(compName), m_registers{}, m_pointer(0), m_transactions(0) {
    // 0x6D60 is 28000 counts, which the flight driver reads as 14.0 V; 0x01F4 is 500 mA
    m_registers[REGISTER_VOLTAGE] = 0x6D;
    m_registers[REGISTER_VOLTAGE + 1] = 0x60;
    m_registers[REGISTER_CURRENT] = 0x01;
    m_registers[REGISTER_CURRENT + 1] = 0xF4;
}

Drv::I2cStatus PowerSim::transaction_handler(FwIndexType portNum,
                                             U32 addr,
                                             Fw::Buffer& writeBuffer,
                                             Fw::Buffer& readBuffer) {
    (void)portNum;
    (void)addr;
    std::lock_guard<std::mutex> lock(m_lock);
    // A write latches the register pointer; any following bytes are stored from it
    const U8* wrote = writeBuffer.getData();
    const FwSizeType wroteSize = writeBuffer.getSize();
    if (wroteSize > 0) {
        m_pointer = wrote[0] % REGISTER_COUNT;
        for (FwSizeType i = 1; i < wroteSize; i++) {
            m_registers[m_pointer] = wrote[i];
            m_pointer = static_cast<U8>((m_pointer + 1) % REGISTER_COUNT);
        }
    }
    U8* read = readBuffer.getData();
    const FwSizeType readSize = readBuffer.getSize();
    for (FwSizeType i = 0; i < readSize; i++) {
        read[i] = m_registers[m_pointer];
        m_pointer = static_cast<U8>((m_pointer + 1) % REGISTER_COUNT);
    }
    this->tlmWrite_Transactions(++m_transactions);
    return Drv::I2cStatus::I2C_OK;
}

void PowerSim::poke_handler(FwIndexType portNum, U8 reg, U8 value) {
    (void)portNum;
    std::lock_guard<std::mutex> lock(m_lock);
    m_registers[reg % REGISTER_COUNT] = value;
}

void PowerSim::setVoltageRaw(U32 raw) {
    std::lock_guard<std::mutex> lock(m_lock);
    m_registers[REGISTER_VOLTAGE] = static_cast<U8>((raw >> 8) & 0xFF);
    m_registers[REGISTER_VOLTAGE + 1] = static_cast<U8>(raw & 0xFF);
}

void PowerSim::setCurrentRaw(U32 raw) {
    std::lock_guard<std::mutex> lock(m_lock);
    m_registers[REGISTER_CURRENT] = static_cast<U8>((raw >> 8) & 0xFF);
    m_registers[REGISTER_CURRENT + 1] = static_cast<U8>(raw & 0xFF);
}

void PowerSim::SET_VOLTAGE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 millivolts) {
    this->setVoltageRaw(millivolts * 2);  // 2000 counts per volt
    this->tlmWrite_BusVoltage(millivolts / 1000.0f);
    this->log_ACTIVITY_HI_VoltageSet(millivolts);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void PowerSim::SET_CURRENT_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 milliamps) {
    this->setCurrentRaw(milliamps);
    this->tlmWrite_Current(milliamps);
    this->log_ACTIVITY_HI_CurrentSet(milliamps);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace PowerSim
