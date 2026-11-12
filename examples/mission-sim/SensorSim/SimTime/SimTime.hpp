#ifndef SENSORSIM_SIM_TIME_HPP
#define SENSORSIM_SIM_TIME_HPP

#include "SensorSim/SimTime/SimTimeComponentAc.hpp"

namespace SensorSim {

class SimTime final : public SimTimeComponentBase {
  public:
    explicit SimTime(const char* compName);
    ~SimTime() = default;

  private:
    void timeGetPort_handler(FwIndexType portNum, Fw::Time& time) override;
};

}  // namespace SensorSim

#endif
