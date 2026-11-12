// ======================================================================
// \title  SimTick.cpp
// \brief  Simulation tick source
// ======================================================================
#include <Wsts/Svc/SimTick/SimTick.hpp>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>

#include <Os/RawTime.hpp>
#include <Wsts/Core/Control.hpp>

namespace Wsts {

SimTick::SimTick(const char* compName)
    : SimTickComponentBase(compName), m_threaded(false), m_tickHz(60), m_quit(false) {
    // Registered at construction so the runtime knows not to start its fallback ticker
    Control::get().registerTickSource();
}

SimTick::~SimTick() {
    this->quit();
}

void SimTick::wstsBind(const char* endpoint, const char* options) {
    (void)endpoint;
    m_threaded = (std::strstr(options, "mode=thread") != nullptr);
    const char* hz = std::strstr(options, "tick_hz=");
    if (hz != nullptr) {
        const long value = std::strtol(hz + 8, nullptr, 0);
        if (value > 0) {
            m_tickHz = static_cast<FwSizeType>(value);
        }
    }
}

void SimTick::startTimer(const Fw::TimeInterval& interval) {
    this->begin(static_cast<U64>(interval.getSeconds()) * 1000000u + interval.getUSeconds());
}

void SimTick::startTimer(FwSizeType microseconds) {
    this->begin(microseconds);
}

void SimTick::begin(U64 microseconds) {
    m_quit.store(false);
    // Cycle sources with their own rate are fed from the simulated time of each tick
    Control::get().setTickPeriod(microseconds * 1000u);
    if (m_threaded) {
        if (!m_thread.joinable()) {
            m_thread = std::thread([this, microseconds]() { this->loop(microseconds); });
        }
    } else {
        this->loop(microseconds);
    }
}

void SimTick::loop(U64 microseconds) {
    Control& control = Control::get();
    auto next = std::chrono::steady_clock::now();
    while (!m_quit.load()) {
        // Simulated time advances `rate` times faster than the wall clock
        const double rate = std::max(0.001, control.rate());
        next += std::chrono::microseconds(static_cast<std::int64_t>(static_cast<double>(microseconds) / rate));
        const auto now = std::chrono::steady_clock::now();
        if (next < now) {
            next = now;  // fell behind (host load, long pause): do not burst to catch up
        }
        std::this_thread::sleep_until(next);
        if (m_quit.load()) {
            break;
        }
        if (control.paused()) {
            control.onIdle();
            continue;
        }
        control.onTick();
        if (this->isConnected_CycleOut_OutputPort(0)) {
            Os::RawTime cycleStart;
            (void)cycleStart.now();
            this->CycleOut_out(0, cycleStart);
        }
    }
}

void SimTick::quit() {
    m_quit.store(true);
    if (m_thread.joinable() && m_thread.get_id() != std::this_thread::get_id()) {
        m_thread.join();
    }
}

}  // namespace Wsts
