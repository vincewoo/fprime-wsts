// ======================================================================
// \title  Clock.cpp
// \brief  Cycle sources that run at their own rate in simulated time
// ======================================================================
#include <Wsts/Core/Clock.hpp>

#include <algorithm>
#include <cmath>
#include <cstdio>

#include <Wsts/Core/Control.hpp>

namespace Wsts {

namespace {
const std::uint64_t PICO = 1000000000000ull;  // milli-hertz times nanoseconds per cycle
//! How long the clock thread sleeps at most before it looks at the run state again
const std::chrono::milliseconds SLICE(2);
}  // namespace

void CycleSchedule::advance(std::uint64_t periodNs, std::vector<std::uint64_t>& offsets) {
    if (m_milliHz == 0) {
        return;
    }
    // Cycle j is at j * PICO / milliHz nanoseconds. 128 bits: the products outgrow 64
    // after a few hours of simulated time at high rates.
    const unsigned __int128 start = m_elapsedNs;
    const unsigned __int128 end = start + periodNs;
    while (static_cast<unsigned __int128>(m_issued) * PICO < end * m_milliHz) {
        const unsigned __int128 at = static_cast<unsigned __int128>(m_issued) * PICO / m_milliHz;
        offsets.push_back(static_cast<std::uint64_t>(at > start ? at - start : 0));
        m_issued++;
    }
    m_elapsedNs += periodNs;
}

CycleClock::~CycleClock() {
    this->stop();
}

bool CycleClock::configure(double hz, Callback callback) {
    if (!(hz > 0.0) || hz > MAX_HZ || m_running.load()) {
        return false;
    }
    m_schedule = CycleSchedule(static_cast<std::uint64_t>(std::llround(hz * 1000.0)));
    m_callback = std::move(callback);
    return m_schedule.milliHz() > 0;
}

void CycleClock::start() {
    if (m_running.exchange(true)) {
        return;
    }
    m_quit.store(false);
    m_thread = std::thread([this]() { this->loop(); });
    Control::get().addClock(this);
}

void CycleClock::stop() {
    if (!m_running.exchange(false)) {
        return;
    }
    Control::get().removeClock(this);
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_quit.store(true);
        m_due.clear();
    }
    m_wake.notify_all();
    if (m_thread.joinable() && m_thread.get_id() != std::this_thread::get_id()) {
        m_thread.join();
    }
}

std::size_t CycleClock::pending() {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_due.size();
}

void CycleClock::grant(std::uint64_t periodNs, double rate, Time now) {
    std::vector<std::uint64_t> offsets;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_schedule.advance(periodNs, offsets);
        if (offsets.empty()) {
            return;
        }
        // A callback that cannot keep up must not pile cycles up without limit: beyond a
        // few ticks' worth they are dropped and counted
        const std::size_t limit = 3 * offsets.size() + 16;
        const double scale = 1.0 / std::max(0.001, rate);
        for (const std::uint64_t offset : offsets) {
            if (m_due.size() >= limit) {
                if (m_overruns.fetch_add(1) == 0) {
                    std::fprintf(stderr, "[WSTS] a %.3f Hz cycle source cannot keep up: cycles are being dropped\n",
                                 this->hz());
                }
                continue;
            }
            m_due.push_back(now + std::chrono::nanoseconds(static_cast<std::int64_t>(static_cast<double>(offset) * scale)));
        }
    }
    m_wake.notify_one();
}

void CycleClock::loop() {
    Control& control = Control::get();
    while (!m_quit.load()) {
        {
            std::unique_lock<std::mutex> lock(m_mutex);
            m_wake.wait(lock, [this]() { return m_quit.load() || !m_due.empty(); });
        }
        // Wait for the instant of the next cycle in short slices, so that a pause is noticed
        bool fire = false;
        while (!m_quit.load() && !fire) {
            if (control.paused()) {
                // What was granted before the pause waits for the resume and keeps its
                // spacing: nothing is issued in a burst afterwards
                const Time pausedAt = std::chrono::steady_clock::now();
                while (control.paused() && !m_quit.load()) {
                    std::this_thread::sleep_for(SLICE);
                }
                const auto shift = std::chrono::steady_clock::now() - pausedAt;
                std::lock_guard<std::mutex> lock(m_mutex);
                for (Time& time : m_due) {
                    time += shift;
                }
                continue;
            }
            Time due;
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                if (m_due.empty()) {
                    break;  // stopped meanwhile
                }
                due = m_due.front();
            }
            const Time now = std::chrono::steady_clock::now();
            if (now >= due) {
                std::lock_guard<std::mutex> lock(m_mutex);
                if (!m_due.empty()) {
                    m_due.pop_front();
                    fire = true;
                }
            } else {
                std::this_thread::sleep_until(std::min(due, now + SLICE));
            }
        }
        if (fire && !m_quit.load()) {
            if (m_callback) {
                m_callback();
            }
            m_cycles.fetch_add(1);
        }
    }
}

}  // namespace Wsts
