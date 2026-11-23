// ======================================================================
// \title  SunSensor.hpp
// \brief  A sun sensor on SpaceWire, read over RMAP
// ======================================================================
#ifndef Components_SunSensor_HPP
#define Components_SunSensor_HPP

#include "Components/SunSensor/SunSensorComponentAc.hpp"

namespace Components {

class SunSensor final : public SunSensorComponentBase {
  public:
    //! Logical addresses on the payload's SpaceWire network, and the sensor's key
    static constexpr U8 SENSOR_ADDRESS = 0xFE;
    static constexpr U8 PAYLOAD_ADDRESS = 0x67;
    static constexpr U8 SENSOR_KEY = 0x20;
    //! The sensor's memory: integration time (U16), sun angle (I32, millidegrees), big-endian
    static constexpr U32 ADDR_INTEGRATION = 0x04;
    static constexpr U32 ADDR_ANGLE = 0x10;

    explicit SunSensor(const char* compName);

  private:
    void run_handler(FwIndexType portNum, U32 context) override;
    void toBufferDriverReturn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;
    void fromBufferDriver_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;
    void SET_INTEGRATION_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U16 ms) override;

    //! Send one RMAP command with no reply address; `data` for a write
    void send(U8 command, U32 address, U32 length, const U8* data, U32 dataLength);
    //! Check and act on a reply
    void handle(const U8* reply, FwSizeType size);

    U8 m_packet[64];
    U16 m_transaction;
    bool m_readPending;
    U16 m_readTransaction;
    bool m_writePending;
    U16 m_writeTransaction;
    U16 m_writeValue;
    U32 m_replies;
    U32 m_errors;
};

}  // namespace Components

#endif
