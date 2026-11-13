// ======================================================================
// \title  SimRegister.hpp
// \brief  Simulation surrogate for register interface components
// ======================================================================
#ifndef WSTS_SIM_REGISTER_HPP
#define WSTS_SIM_REGISTER_HPP

#include <Wsts/Core/RegisterSurrogate.hpp>
#include <Wsts/Drv/SimRegister/SimRegisterComponentAc.hpp>

namespace Wsts {

class SimRegister final : public SimRegisterComponentBase, public RegisterSurrogate {
  public:
    explicit SimRegister(const char* compName);
    ~SimRegister();

    //! Bind this instance to a WSTS bus endpoint. Called from generated topology code.
    //! Options: see Wsts::RegisterSurrogate::bindRegister ("irq=serial|direct|off").
    void wstsBind(const char* endpoint, const char* options = "");

    //! Stand-ins for what flight topology code calls to open the device: the arguments
    //! (a device name, a base address, an interrupt number) are accepted and ignored
    template <typename... Args>
    void configure(Args&&...) {}
    template <typename... Args>
    bool open(Args&&...) {
        return true;
    }

  private:
    U32 read32In_handler(FwIndexType portNum, U64 offset) override;
    void write32In_handler(FwIndexType portNum, U64 offset, U32 value) override;
    U64 read64In_handler(FwIndexType portNum, U64 offset) override;
    void write64In_handler(FwIndexType portNum, U64 offset, U64 value) override;
};

}  // namespace Wsts

#endif
