// ======================================================================
// \title  BusSpiDevice.cpp
// \brief  Adapter from a WSTS SPI endpoint to an F Prime device simulator
// ======================================================================
#include <Wsts/Drv/BusSpiDevice/BusSpiDevice.hpp>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

BusSpiDevice::BusSpiDevice(const char* compName) : BusSpiDeviceComponentBase(compName) {}

BusSpiDevice::~BusSpiDevice() {
    if (!m_endpoint.empty()) {
        Bus::get().detachSpi(m_endpoint, this);
    }
}

void BusSpiDevice::wstsServe(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
    Bus::get().attachSpi(m_endpoint, this);
}

int BusSpiDevice::transfer(const Bytes& write, Bytes& read) {
    if (!this->isConnected_transfer_OutputPort(0)) {
        // MISO floats, as with no device on the endpoint: the read arrives zeroed
        return 0;
    }
    // No lock is held here, and the buffers wrap the transfer's own memory: the device
    // fills the read in place, and nothing is copied
    Fw::Buffer writeBuffer(const_cast<U8*>(write.data()), static_cast<FwSizeType>(write.size()));
    Fw::Buffer readBuffer(read.data(), static_cast<FwSizeType>(read.size()));
    const Drv::SpiStatus status = this->transfer_out(0, writeBuffer, readBuffer);
    return static_cast<int>(status);
}

}  // namespace Wsts
