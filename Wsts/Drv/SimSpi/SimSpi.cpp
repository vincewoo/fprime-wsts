// ======================================================================
// \title  SimSpi.cpp
// \brief  Simulation surrogate for SPI bus drivers
// ======================================================================
#include <Wsts/Drv/SimSpi/SimSpi.hpp>

#include <cstring>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

SimSpi::SimSpi(const char* compName) : SimSpiComponentBase(compName) {}

void SimSpi::wstsBind(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
}

Drv::SpiStatus SimSpi::SpiWriteRead_handler(FwIndexType portNum, Fw::Buffer& writeBuffer, Fw::Buffer& readBuffer) {
    (void)portNum;
    Bytes write;
    if (writeBuffer.getData() != nullptr) {
        write.assign(writeBuffer.getData(), writeBuffer.getData() + writeBuffer.getSize());
    }
    Bytes read(readBuffer.getSize(), 0);
    const int status = Bus::get().spiTransfer(m_endpoint, write, read);
    if (readBuffer.getData() != nullptr && !read.empty()) {
        (void)std::memcpy(readBuffer.getData(), read.data(), read.size());
    }
    if (status < 0 || status > Drv::SpiStatus::SPI_OTHER_ERR) {
        return Drv::SpiStatus::SPI_OTHER_ERR;
    }
    return static_cast<Drv::SpiStatus::T>(status);
}

void SimSpi::SpiReadWrite_handler(FwIndexType portNum, Fw::Buffer& writeBuffer, Fw::Buffer& readBuffer) {
    (void)this->SpiWriteRead_handler(portNum, writeBuffer, readBuffer);
}

}  // namespace Wsts
