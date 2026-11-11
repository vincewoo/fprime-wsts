// ======================================================================
// \title  SimLinuxGpio.cpp
// \brief  Mirror surrogate for Drv.LinuxGpioDriver
// ======================================================================
#include <Surrogates/SimLinuxGpio/SimLinuxGpio.hpp>

#include <Fw/Types/String.hpp>
#include <Os/RawTime.hpp>
#include <Wsts/Core/Bus.hpp>

namespace Sim {

SimLinuxGpio::SimLinuxGpio(const char* compName)
    : SimLinuxGpioComponentBase(compName), m_configuration(MAX_GPIO_CONFIGURATION) {}

SimLinuxGpio::~SimLinuxGpio() {
    if (!m_endpoint.empty()) {
        Wsts::Bus::get().unbind(m_endpoint);
    }
}

void SimLinuxGpio::wstsBind(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
    Wsts::Bus::get().bindGpio(m_endpoint, [this](bool level) { this->edge(level); });
}

Os::File::Status SimLinuxGpio::open(const char* device, U32 gpio, GpioConfiguration configuration) {
    m_configuration = configuration;
    const Fw::String chip(device);
    const Fw::String label("wsts");
    const Fw::String pin(m_endpoint.c_str());
    this->log_DIAGNOSTIC_OpenChip(chip, label, gpio, pin);
    return Os::File::OP_OK;
}

Drv::GpioStatus SimLinuxGpio::toStatus(int status) {
    if (status == 0) {
        return Drv::GpioStatus::OP_OK;
    }
    if (status < 0 || status > Drv::GpioStatus::UNKNOWN_ERROR) {
        return Drv::GpioStatus::UNKNOWN_ERROR;
    }
    return static_cast<Drv::GpioStatus::T>(status);
}

Drv::GpioStatus SimLinuxGpio::gpioWrite_handler(FwIndexType portNum, const Fw::Logic& state) {
    (void)portNum;
    if (m_configuration != GPIO_OUTPUT) {
        return Drv::GpioStatus::INVALID_MODE;  // as the flight driver answers for an input pin
    }
    return toStatus(Wsts::Bus::get().gpioWrite(m_endpoint, state == Fw::Logic::HIGH));
}

Drv::GpioStatus SimLinuxGpio::gpioRead_handler(FwIndexType portNum, Fw::Logic& state) {
    (void)portNum;
    if (m_configuration != GPIO_INPUT) {
        return Drv::GpioStatus::INVALID_MODE;  // the flight driver reads only GPIO_INPUT pins
    }
    bool level = false;
    const int status = Wsts::Bus::get().gpioRead(m_endpoint, level);
    if (status == 0) {
        state = level ? Fw::Logic::HIGH : Fw::Logic::LOW;
    }
    return toStatus(status);
}

void SimLinuxGpio::edge(bool level) {
    const bool wanted = (m_configuration == GPIO_INTERRUPT_BOTH_RISING_AND_FALLING_EDGES) ||
                        (m_configuration == GPIO_INTERRUPT_RISING_EDGE && level) ||
                        (m_configuration == GPIO_INTERRUPT_FALLING_EDGE && !level);
    if (wanted && this->isConnected_gpioInterrupt_OutputPort(0)) {
        Os::RawTime now;
        (void)now.now();
        this->gpioInterrupt_out(0, now);
    }
}

}  // namespace Sim
