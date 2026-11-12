#ifndef SENSORSIM_GYRO_SIM_HPP
#define SENSORSIM_GYRO_SIM_HPP

#include "SensorSim/GyroSim/GyroSimComponentAc.hpp"

namespace SensorSim {

class GyroSim final : public GyroSimComponentBase {
  public:
    explicit GyroSim(const char* compName);
    ~GyroSim() = default;

    //! Called from topology phase code.
    //! \param stepHz: the rate this model is stepped at
    //! \param profileFile: file whose first line names the motion profile, or nullptr
    void configure(U32 stepHz, const char* profileFile);

  private:
    void schedIn_handler(FwIndexType portNum, U32 context) override;

    U32 m_stepHz;
    U32 m_steps;
    SimCommon::Motion m_motion;
    char m_source[80];
    bool m_announced;
};

}  // namespace SensorSim

#endif
