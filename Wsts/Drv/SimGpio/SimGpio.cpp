// ======================================================================
// \title  SimGpio.cpp
// \brief  Simulation surrogate for a GPIO pin driver
// ======================================================================
#include <Wsts/Drv/SimGpio/SimGpio.hpp>

#include <Os/RawTime.hpp>
#include <Wsts/Core/Bus.hpp>

namespace Wsts {

SimGpio::SimGpio(const char* compName)
    : SimGpioComponentBase(compName), m_configuration(GPIO_INTERRUPT_BOTH_RISING_AND_FALLING_EDGES) {}

SimGpio::~SimGpio() {
    if (!m_endpoint.empty()) {
        Bus::get().unbind(m_endpoint);
    }
}

void SimGpio::wstsBind(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
    Bus::get().bindGpio(m_endpoint, [this](bool level) { this->edge(level); });
}

Drv::GpioStatus SimGpio::toStatus(int status) {
    if (status == 0) {
        return Drv::GpioStatus::OP_OK;
    }
    if (status < 0 || status > Drv::GpioStatus::UNKNOWN_ERROR) {
        return Drv::GpioStatus::UNKNOWN_ERROR;
    }
    return static_cast<Drv::GpioStatus::T>(status);
}

Drv::GpioStatus SimGpio::gpioWrite_handler(FwIndexType portNum, const Fw::Logic& state) {
    (void)portNum;
    return toStatus(Bus::get().gpioWrite(m_endpoint, state == Fw::Logic::HIGH));
}

Drv::GpioStatus SimGpio::gpioRead_handler(FwIndexType portNum, Fw::Logic& state) {
    (void)portNum;
    bool level = false;
    const int status = Bus::get().gpioRead(m_endpoint, level);
    if (status == 0) {
        state = level ? Fw::Logic::HIGH : Fw::Logic::LOW;
    }
    return toStatus(status);
}

void SimGpio::edge(bool level) {
    const bool wanted = (m_configuration == GPIO_INTERRUPT_BOTH_RISING_AND_FALLING_EDGES) ||
                        (m_configuration == GPIO_INTERRUPT_RISING_EDGE && level) ||
                        (m_configuration == GPIO_INTERRUPT_FALLING_EDGE && !level);
    if (wanted && this->isConnected_gpioInterrupt_OutputPort(0)) {
        Os::RawTime now;
        (void)now.now();
        this->gpioInterrupt_out(0, now);
    }
}

}  // namespace Wsts
