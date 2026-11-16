// ======================================================================
// \title  BusGpioDevice.cpp
// \brief  Adapter from a WSTS GPIO endpoint to an F Prime device simulator
// ======================================================================
#include <Wsts/Drv/BusGpioDevice/BusGpioDevice.hpp>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

BusGpioDevice::BusGpioDevice(const char* compName) : BusGpioDeviceComponentBase(compName) {}

BusGpioDevice::~BusGpioDevice() {
    if (!m_endpoint.empty()) {
        Bus::get().detachGpio(m_endpoint, this);
    }
}

void BusGpioDevice::wstsServe(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
    Bus::get().attachGpio(m_endpoint, this);
}

void BusGpioDevice::onWrite(bool level) {
    // No lock is held here: a simulator whose pinWrite port is synchronous may act on the
    // level from inside the call
    if (this->isConnected_pinWrite_OutputPort(0)) {
        (void)this->pinWrite_out(0, level ? Fw::Logic::HIGH : Fw::Logic::LOW);
    }
}

Drv::GpioStatus BusGpioDevice::gpioWrite_handler(FwIndexType portNum, const Fw::Logic& state) {
    (void)portNum;
    // The device drives the line towards flight software: the level changes there, and an
    // edge raises the flight side's interrupt. Recorded as model traffic, through the faults
    Bus::get().gpioSetInput(m_endpoint, state == Fw::Logic::HIGH, "model");
    return Drv::GpioStatus::OP_OK;
}

Drv::GpioStatus BusGpioDevice::gpioRead_handler(FwIndexType portNum, Fw::Logic& state) {
    (void)portNum;
    state = Bus::get().gpioOutput(m_endpoint) ? Fw::Logic::HIGH : Fw::Logic::LOW;
    return Drv::GpioStatus::OP_OK;
}

}  // namespace Wsts
