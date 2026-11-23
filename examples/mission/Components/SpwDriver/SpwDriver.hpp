// ======================================================================
// \title  SpwDriver.hpp
// \brief  The payload board's SpaceWire controller (Drv.PassiveBufferDriver)
// ======================================================================
#ifndef Components_SpwDriver_HPP
#define Components_SpwDriver_HPP

#include "Components/SpwDriver/SpwDriverComponentAc.hpp"

namespace Components {

class SpwDriver final : public SpwDriverComponentBase {
  public:
    explicit SpwDriver(const char* compName);

    //! Bring the link up on the controller's `link` with this node's logical address
    bool configure(U8 link, U8 logicalAddress);

  private:
    void bufferIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;
    void bufferOutReturn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;

    U8 m_link;
    U8 m_logicalAddress;
};

}  // namespace Components

#endif
