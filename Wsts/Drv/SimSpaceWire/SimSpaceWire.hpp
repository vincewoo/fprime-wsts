// ======================================================================
// \title  SimSpaceWire.hpp
// \brief  Simulation surrogate for SpaceWire drivers (Drv.PassiveBufferDriver)
// ======================================================================
#ifndef WSTS_SIM_SPACE_WIRE_HPP
#define WSTS_SIM_SPACE_WIRE_HPP

#include <string>

#include <Os/Mutex.hpp>
#include <Wsts/Core/Json.hpp>
#include <Wsts/Drv/SimSpaceWire/SimSpaceWireComponentAc.hpp>

namespace Wsts {

class SimSpaceWire final : public SimSpaceWireComponentBase {
  public:
    explicit SimSpaceWire(const char* compName);
    ~SimSpaceWire();

    //! Bind this instance to a WSTS packet endpoint. Called from generated topology code.
    void wstsBind(const char* endpoint, const char* options = "");

    //! Stand-ins for the set-up calls of a SpaceWire driver (its link, its logical address,
    //! its DMA channels): the arguments are accepted and ignored, so that topology code
    //! written against the flight driver compiles unchanged
    template <typename... Args>
    bool open(Args&&...) {
        return true;
    }
    template <typename... Args>
    bool configure(Args&&...) {
        return true;
    }
    template <typename... Args>
    void start(Args&&...) {}
    void stop() {}

  private:
    void bufferIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;
    void bufferOutReturn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;
    void run_handler(FwIndexType portNum, U32 context) override;

    //! Deliver a packet arriving from the network to flight software
    void receive(const Bytes& packet);

    std::string m_endpoint;
    Os::Mutex m_receiveLock;
};

}  // namespace Wsts

#endif
