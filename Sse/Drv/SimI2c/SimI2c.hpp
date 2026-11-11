// ======================================================================
// \title  SimI2c.hpp
// \brief  Simulation surrogate for I2C bus drivers
// ======================================================================
#ifndef SSE_SIM_I2C_HPP
#define SSE_SIM_I2C_HPP

#include <string>

#include <Sse/Drv/SimI2c/SimI2cComponentAc.hpp>

namespace Sse {

class SimI2c final : public SimI2cComponentBase {
  public:
    explicit SimI2c(const char* compName);
    ~SimI2c() = default;

    //! Bind this instance to an SSE bus endpoint. Called from generated topology code.
    void sseBind(const char* endpoint, const char* options = "");

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

}  // namespace Sse

#endif
