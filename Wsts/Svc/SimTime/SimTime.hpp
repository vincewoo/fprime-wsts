// ======================================================================
// \title  SimTime.hpp
// \brief  Time source of a simulated deployment: the host's clock, or simulated time
// ======================================================================
#ifndef WSTS_SIM_TIME_HPP
#define WSTS_SIM_TIME_HPP

#include <Wsts/Svc/SimTime/SimTimeComponentAc.hpp>

namespace Wsts {

class SimTime final : public SimTimeComponentBase {
  public:
    explicit SimTime(const char* compName);
    ~SimTime() = default;

  private:
    void timeGetPort_handler(FwIndexType portNum, Fw::Time& time) override;
};

}  // namespace Wsts

#endif
