// ======================================================================
// \title  SimTick.hpp
// \brief  Simulation tick source
// ======================================================================
#ifndef SSE_SIM_TICK_HPP
#define SSE_SIM_TICK_HPP

#include <atomic>
#include <thread>

#include <Fw/Time/TimeInterval.hpp>
#include <Sse/Svc/SimTick/SimTickComponentAc.hpp>

namespace Sse {

class SimTick final : public SimTickComponentBase {
  public:
    explicit SimTick(const char* compName);
    ~SimTick();

    //! Called from generated topology code. Options:
    //!   "mode=block"   startTimer() cycles on the calling thread until quit() (Svc.LinuxTimer)
    //!   "mode=thread"  startTimer() returns at once and cycles on its own thread, like a timer
    //!                  that fires from an interrupt (VxWorks watchdog timer)
    //!   "tick_hz=<n>"  system clock rate assumed by startTimerTick()
    void sseBind(const char* endpoint, const char* options = "");

    // Stand-ins for the timer APIs this surrogate replaces
    void startTimer(const Fw::TimeInterval& interval);
    void startTimer(FwSizeType microseconds);
    template <typename Ticks>
    void startTimerTick(Ticks ticks) {
        this->startTimer(static_cast<FwSizeType>(ticks) * 1000000u / m_tickHz);
    }
    void quit();

  private:
    void begin(U64 microseconds);
    void loop(U64 microseconds);

    bool m_threaded;
    FwSizeType m_tickHz;
    std::atomic<bool> m_quit;
    std::thread m_thread;
};

}  // namespace Sse

#endif
