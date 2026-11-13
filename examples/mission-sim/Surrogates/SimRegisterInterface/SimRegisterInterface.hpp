// ======================================================================
// \title  SimRegisterInterface.hpp
// \brief  Mirror surrogate for SensorLib.RegisterInterface
// ======================================================================
#ifndef SIM_SIM_REGISTER_INTERFACE_HPP
#define SIM_SIM_REGISTER_INTERFACE_HPP

#include <Surrogates/SimRegisterInterface/SimRegisterInterfaceComponentAc.hpp>
#include <Wsts/Core/RegisterSurrogate.hpp>

namespace Sim {

//! The flight component's ports, commands and events; the registers are a block on the WSTS bus
class SimRegisterInterface final : public SimRegisterInterfaceComponentBase, public Wsts::RegisterSurrogate {
  public:
    explicit SimRegisterInterface(const char* compName);
    ~SimRegisterInterface();

    //! Bind this instance to a WSTS bus endpoint. Called from generated topology code.
    void wstsBind(const char* endpoint, const char* options = "");

    //! What flight topology code calls to open the device: there is none to open
    void configure(const char* device);

  private:
    U32 read32In_handler(FwIndexType portNum, U64 offset) override;
    void write32In_handler(FwIndexType portNum, U64 offset, U32 value) override;
    U64 read64In_handler(FwIndexType portNum, U64 offset) override;
    void write64In_handler(FwIndexType portNum, U64 offset, U64 value) override;

    void REG_READ_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset) override;
    void REG_WRITE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset, U32 value) override;
};

}  // namespace Sim

#endif
