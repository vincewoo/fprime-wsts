// ======================================================================
// \title  SimTime.hpp
// \brief  Time source of a simulated deployment: the host's clock, or simulated time
// ======================================================================
#ifndef WSTS_SIM_TIME_HPP
#define WSTS_SIM_TIME_HPP

#include <Os/RawTime.hpp>
#include <Wsts/Svc/SimTime/SimTimeComponentAc.hpp>

namespace Wsts {

class SimTime final : public SimTimeComponentBase {
  public:
    explicit SimTime(const char* compName);
    ~SimTime() = default;

    //! Stand-in for Svc.OsTime's set_epoch(), which a flight topology's set-up code calls
    //! directly. The epoch is recorded and otherwise ignored: SimTime stays the time
    //! authority, the host's clock in real time and simulated time in stepped time
    void set_epoch(const Fw::Time& fw_time, const Os::RawTime& os_time);

  private:
    void timeGetPort_handler(FwIndexType portNum, Fw::Time& time) override;
    void setEpoch_handler(FwIndexType portNum, const Fw::Time& fw_time, const Os::RawTime& os_time) override;
    void SetCurrentTime_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 seconds_now) override;

    Fw::Time m_epoch;
};

}  // namespace Wsts

#endif
