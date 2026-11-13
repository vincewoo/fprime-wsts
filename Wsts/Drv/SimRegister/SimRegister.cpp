// ======================================================================
// \title  SimRegister.cpp
// \brief  Simulation surrogate for register interface components
// ======================================================================
#include <Wsts/Drv/SimRegister/SimRegister.hpp>

namespace Wsts {

SimRegister::SimRegister(const char* compName) : SimRegisterComponentBase(compName) {}

SimRegister::~SimRegister() {
    // Before the ports go away: no interrupt is delivered into a component being destroyed
    this->unbindRegister();
}

void SimRegister::wstsBind(const char* endpoint, const char* options) {
    this->bindRegister(endpoint, options, [this](std::uint32_t number) {
        if (this->isConnected_interrupt_OutputPort(0)) {
            this->interrupt_out(0, number);
        }
    });
}

U32 SimRegister::read32In_handler(FwIndexType portNum, U64 offset) {
    (void)portNum;
    return this->registerRead32(offset);
}

void SimRegister::write32In_handler(FwIndexType portNum, U64 offset, U32 value) {
    (void)portNum;
    this->registerWrite32(offset, value);
}

U64 SimRegister::read64In_handler(FwIndexType portNum, U64 offset) {
    (void)portNum;
    return this->registerRead64(offset);
}

void SimRegister::write64In_handler(FwIndexType portNum, U64 offset, U64 value) {
    (void)portNum;
    this->registerWrite64(offset, value);
}

}  // namespace Wsts
