// ======================================================================
// \title  Clock.hpp
// \brief  Cycle sources that run at their own rate in simulated time
//
// A simulator may need to be cycled at a rate the flight software has no rate group for:
// faster than the flight tick, or not a multiple of it. A CycleClock produces cycles at
// such a rate. It does not keep time itself. The control core tells it, on every
// simulation tick, how much simulated time the tick covers, and the clock works out which
// of its cycles fall inside that interval (CycleSchedule: integer arithmetic, so the count
// never drifts). The clock's own thread then issues them, each at its instant within the
// tick. So a clock stops when ticks stop (pause), follows the time scale, and takes its
// phase from the tick source, whatever that is.
// ======================================================================
#ifndef WSTS_CORE_CLOCK_HPP
#define WSTS_CORE_CLOCK_HPP

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

namespace Wsts {

//! When the cycles of a clock fall in simulated time. Cycle j is at j / rate seconds after
//! the clock's first tick: cycle 0 coincides with that tick.
class CycleSchedule {
  public:
    //! \param milliHz: rate in thousandths of a hertz
    explicit CycleSchedule(std::uint64_t milliHz = 0) : m_milliHz(milliHz) {}

    //! Simulated time advances by `periodNs`. Appends, for every cycle that falls in the
    //! interval, its offset in nanoseconds from the start of the interval.
    void advance(std::uint64_t periodNs, std::vector<std::uint64_t>& offsets);

    //! Cycles issued so far
    std::uint64_t issued() const { return m_issued; }
    std::uint64_t milliHz() const { return m_milliHz; }

  private:
    std::uint64_t m_milliHz;
    std::uint64_t m_elapsedNs = 0;
    std::uint64_t m_issued = 0;
};

class CycleClock {
  public:
    using Callback = std::function<void()>;
    using Time = std::chrono::steady_clock::time_point;

    //! Highest rate accepted
    static constexpr double MAX_HZ = 100000.0;

    CycleClock() = default;
    ~CycleClock();
    CycleClock(const CycleClock&) = delete;
    CycleClock& operator=(const CycleClock&) = delete;

    //! Set the rate and what to call on each cycle. False if the rate is not in (0, MAX_HZ].
    bool configure(double hz, Callback callback);
    //! Register with the control core and start the thread that issues cycles
    void start();
    void stop();

    //! Called by the control core on each simulation tick, on the tick thread: simulated
    //! time advances by `periodNs`, which takes `periodNs / rate` of wall time from `now`.
    void grant(std::uint64_t periodNs, double rate, Time now);

    //! Cycles issued (the callback has returned)
    std::uint64_t cycles() const { return m_cycles.load(); }
    //! Cycles dropped because the callback could not keep up
    std::uint64_t overruns() const { return m_overruns.load(); }
    //! Cycles granted and not yet issued
    std::size_t pending();
    double hz() const { return static_cast<double>(m_schedule.milliHz()) / 1000.0; }

  private:
    void loop();

    CycleSchedule m_schedule;
    Callback m_callback;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::deque<Time> m_due;  //!< wall times of the cycles granted and not yet issued
    std::thread m_thread;
    std::atomic<bool> m_quit{false};
    std::atomic<bool> m_running{false};
    std::atomic<std::uint64_t> m_cycles{0};
    std::atomic<std::uint64_t> m_overruns{0};
};

}  // namespace Wsts

#endif
