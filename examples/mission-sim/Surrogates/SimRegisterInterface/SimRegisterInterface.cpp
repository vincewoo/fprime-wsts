// ======================================================================
// \title  SimRegisterInterface.cpp
// \brief  Mirror surrogate for SensorLib.RegisterInterface
// ======================================================================
#include <Surrogates/SimRegisterInterface/SimRegisterInterface.hpp>

namespace Sim {

SimRegisterInterface::SimRegisterInterface(const char* compName) : SimRegisterInterfaceComponentBase(compName) {}

SimRegisterInterface::~SimRegisterInterface() {
    this->unbindRegister();  // no interrupt is delivered into a component being destroyed
}

void SimRegisterInterface::wstsBind(const char* endpoint, const char* options) {
    this->bindRegister(endpoint, options, [this](std::uint32_t number) {
        if (this->isConnected_interrupt_OutputPort(0)) {
            this->interrupt_out(0, number);
        }
    });
}

void SimRegisterInterface::configure(const char* device) {
    (void)device;
}

// ---- The register ports: the bus side is the helper's ----

U32 SimRegisterInterface::read32In_handler(FwIndexType portNum, U64 offset) {
    (void)portNum;
    return this->registerRead32(offset);
}

void SimRegisterInterface::write32In_handler(FwIndexType portNum, U64 offset, U32 value) {
    (void)portNum;
    this->registerWrite32(offset, value);
}

U64 SimRegisterInterface::read64In_handler(FwIndexType portNum, U64 offset) {
    (void)portNum;
    return this->registerRead64(offset);
}

void SimRegisterInterface::write64In_handler(FwIndexType portNum, U64 offset, U64 value) {
    (void)portNum;
    this->registerWrite64(offset, value);
}

// ---- The flight component's commands: the same events, with the access made on the bus ----

void SimRegisterInterface::REG_READ_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset) {
    std::uint64_t value = 0;
    if (this->registerRead(offset, 4, value) != 0) {
        this->log_WARNING_LO_OffsetOutOfRange(offset);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    this->log_ACTIVITY_HI_RegisterValue(offset, static_cast<U32>(value));
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void SimRegisterInterface::REG_WRITE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset, U32 value) {
    if (this->registerWrite(offset, 4, value) != 0) {
        this->log_WARNING_LO_OffsetOutOfRange(offset);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    this->log_ACTIVITY_HI_RegisterWritten(offset, value);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Sim
