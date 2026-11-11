// ======================================================================
// \title  SimI2c.cpp
// \brief  Simulation surrogate for I2C bus drivers
// ======================================================================
#include <Sse/Drv/SimI2c/SimI2c.hpp>

#include <cstring>

#include <Sse/Core/Bus.hpp>

namespace Sse {

SimI2c::SimI2c(const char* compName) : SimI2cComponentBase(compName) {}

void SimI2c::sseBind(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
}

Drv::I2cStatus SimI2c::transact(U32 addr, Fw::Buffer* writeBuffer, Fw::Buffer* readBuffer) {
    Bytes write;
    if (writeBuffer != nullptr && writeBuffer->getData() != nullptr) {
        write.assign(writeBuffer->getData(), writeBuffer->getData() + writeBuffer->getSize());
    }
    Bytes read((readBuffer != nullptr) ? readBuffer->getSize() : 0, 0);
    const int status = Bus::get().i2cTransact(m_endpoint, addr, write, read);
    if (readBuffer != nullptr && readBuffer->getData() != nullptr && !read.empty()) {
        (void)std::memcpy(readBuffer->getData(), read.data(), read.size());
    }
    if (status < 0 || status > Drv::I2cStatus::I2C_OTHER_ERR) {
        return Drv::I2cStatus::I2C_OTHER_ERR;
    }
    return static_cast<Drv::I2cStatus::T>(status);
}

Drv::I2cStatus SimI2c::write_handler(FwIndexType portNum, U32 addr, Fw::Buffer& serBuffer) {
    (void)portNum;
    return this->transact(addr, &serBuffer, nullptr);
}

Drv::I2cStatus SimI2c::read_handler(FwIndexType portNum, U32 addr, Fw::Buffer& serBuffer) {
    (void)portNum;
    return this->transact(addr, nullptr, &serBuffer);
}

Drv::I2cStatus SimI2c::writeRead_handler(FwIndexType portNum,
                                         U32 addr,
                                         Fw::Buffer& writeBuffer,
                                         Fw::Buffer& readBuffer) {
    (void)portNum;
    return this->transact(addr, &writeBuffer, &readBuffer);
}

}  // namespace Sse
