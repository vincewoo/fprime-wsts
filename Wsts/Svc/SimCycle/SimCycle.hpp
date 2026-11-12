// ======================================================================
// \title  SimCycle.hpp
// \brief  Cycle source with its own rate in simulated time
// ======================================================================
#ifndef WSTS_SIM_CYCLE_HPP
#define WSTS_SIM_CYCLE_HPP

#include <Wsts/Core/Clock.hpp>
#include <Wsts/Svc/SimCycle/SimCycleComponentAc.hpp>

namespace Wsts {

class SimCycle final : public SimCycleComponentBase {
  public:
    explicit SimCycle(const char* compName);
    ~SimCycle();

    //! Set the rate and start. Called by generated set-up code. Cycles begin with the
    //! first simulation tick after this call. Options: none yet.
    void wstsClock(double hz, const char* options = "");

    //! Cycles issued so far, and cycles dropped because the receivers could not keep up
    U64 cycles() const { return m_clock.cycles(); }
    U64 overruns() const { return m_clock.overruns(); }

  private:
    CycleClock m_clock;
};

}  // namespace Wsts

#endif
