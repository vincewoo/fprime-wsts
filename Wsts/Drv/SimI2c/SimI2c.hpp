// ======================================================================
// \title  SimI2c.hpp
// \brief  Simulation surrogate for I2C bus drivers
// ======================================================================
#ifndef WSTS_SIM_I2C_HPP
#define WSTS_SIM_I2C_HPP

#include <string>

#include <Wsts/Drv/SimI2c/SimI2cComponentAc.hpp>

namespace Wsts {

class SimI2c final : public SimI2cComponentBase {
  public:
    explicit SimI2c(const char* compName);
    ~SimI2c() = default;

    //! Bind this instance to a WSTS bus endpoint. Called from generated topology code.
    void wstsBind(const char* endpoint, const char* options = "");

    //! Stand-in for the driver's open(): the device arguments are accepted and ignored
    template <typename... Args>
    bool open(Args&&...) {
        return true;
    }

  private:
    Drv::I2cStatus write_handler(FwIndexType portNum, U32 addr, Fw::Buffer& serBuffer) override;
    Drv::I2cStatus read_handler(FwIndexType portNum, U32 addr, Fw::Buffer& serBuffer) override;
    Drv::I2cStatus writeRead_handler(FwIndexType portNum,
                                     U32 addr,
                                     Fw::Buffer& writeBuffer,
                                     Fw::Buffer& readBuffer) override;

    Drv::I2cStatus transact(U32 addr, Fw::Buffer* writeBuffer, Fw::Buffer* readBuffer);

    std::string m_endpoint;
};

}  // namespace Wsts

#endif
