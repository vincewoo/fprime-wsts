#ifndef SENSORSIM_SERIAL_SENSOR_SIM_HPP
#define SENSORSIM_SERIAL_SENSOR_SIM_HPP

#include "SensorSim/SerialSensorSim/SerialSensorSimComponentAc.hpp"

namespace SensorSim {

class SerialSensorSim final : public SerialSensorSimComponentBase {
  public:
    explicit SerialSensorSim(const char* compName);
    ~SerialSensorSim() = default;

    //! Called from topology phase code
    void configure(const char* reportPrefix);

  private:
    void drvConnected_handler(FwIndexType portNum) override;
    void drvReceiveIn_handler(FwIndexType portNum, Fw::Buffer& buffer, const Drv::ByteStreamStatus& status) override;
    void schedIn_handler(FwIndexType portNum, U32 context) override;
    void SET_BIAS_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, F32 bias) override;

    void sendReport();

    const char* m_prefix;
    bool m_connected;
    F32 m_bias;
    U32 m_reports;
    U32 m_queries;
};

}  // namespace SensorSim

#endif
