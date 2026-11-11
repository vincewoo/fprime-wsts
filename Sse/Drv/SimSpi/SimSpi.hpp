// ======================================================================
// \title  SimSpi.hpp
// \brief  Simulation surrogate for SPI bus drivers
// ======================================================================
#ifndef SSE_SIM_SPI_HPP
#define SSE_SIM_SPI_HPP

#include <string>

#include <Sse/Drv/SimSpi/SimSpiComponentAc.hpp>

namespace Sse {

class SimSpi final : public SimSpiComponentBase {
  public:
    explicit SimSpi(const char* compName);
    ~SimSpi() = default;

    //! Bind this instance to an SSE bus endpoint. Called from generated topology code.
    void sseBind(const char* endpoint, const char* options = "");

    //! Stand-in for the driver's open(): the device arguments are accepted and ignored
    template <typename... Args>
    bool open(Args&&...) {
        return true;
    }

  private:
    Drv::SpiStatus SpiWriteRead_handler(FwIndexType portNum, Fw::Buffer& writeBuffer, Fw::Buffer& readBuffer) override;
    void SpiReadWrite_handler(FwIndexType portNum, Fw::Buffer& writeBuffer, Fw::Buffer& readBuffer) override;

    std::string m_endpoint;
};

}  // namespace Sse

#endif
