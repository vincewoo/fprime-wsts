#ifndef COMPONENTS_SERIAL_SENSOR_HPP
#define COMPONENTS_SERIAL_SENSOR_HPP

#include "Components/SerialSensor/SerialSensorComponentAc.hpp"

namespace Components {

class SerialSensor final : public SerialSensorComponentBase {
  public:
    explicit SerialSensor(const char* compName);
    ~SerialSensor() = default;

  private:
    void drvConnected_handler(FwIndexType portNum) override;
    void drvReceiveIn_handler(FwIndexType portNum, Fw::Buffer& buffer, const Drv::ByteStreamStatus& status) override;
    void SEND_QUERY_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& query) override;

    void handleLine();

    char m_line[64];
    U32 m_length;
    U32 m_lines;
    U32 m_badLines;
};

}  // namespace Components

#endif
