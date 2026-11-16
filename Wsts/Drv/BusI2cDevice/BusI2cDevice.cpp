// ======================================================================
// \title  BusI2cDevice.cpp
// \brief  Adapter from a WSTS I2C endpoint to an F Prime device simulator
// ======================================================================
#include <Wsts/Drv/BusI2cDevice/BusI2cDevice.hpp>

#include <cstdlib>
#include <cstring>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

BusI2cDevice::BusI2cDevice(const char* compName) : BusI2cDeviceComponentBase(compName), m_addr(0) {}

BusI2cDevice::~BusI2cDevice() {
    if (!m_endpoint.empty()) {
        Bus::get().detachI2c(m_endpoint, m_addr, this);
    }
}

void BusI2cDevice::wstsServe(const char* endpoint, const char* options) {
    m_endpoint = endpoint;
    const char* addr = std::strstr(options, "addr=");
    if (addr != nullptr) {
        m_addr = static_cast<std::uint32_t>(std::strtoul(addr + 5, nullptr, 0));
    }
    Bus::get().attachI2c(m_endpoint, m_addr, this);
}

int BusI2cDevice::transact(std::uint32_t addr, const Bytes& write, Bytes& read) {
    if (!this->isConnected_transact_OutputPort(0)) {
        // Nobody is home at this address, as far as flight software can tell
        return 1;  // Drv::I2cStatus::I2C_ADDRESS_ERR
    }
    // No lock is held here, and the buffers wrap the transaction's own memory: the device
    // fills the read in place, and nothing is copied
    Fw::Buffer writeBuffer(const_cast<U8*>(write.data()), static_cast<FwSizeType>(write.size()));
    Fw::Buffer readBuffer(read.data(), static_cast<FwSizeType>(read.size()));
    const Drv::I2cStatus status = this->transact_out(0, addr, writeBuffer, readBuffer);
    return static_cast<int>(status);
}

bool BusI2cDevice::pokeRegister(std::uint8_t reg, std::uint8_t value) {
    if (!this->isConnected_poke_OutputPort(0)) {
        return false;
    }
    this->poke_out(0, reg, value);
    return true;
}

}  // namespace Wsts
