// ======================================================================
// \title  SimCycle.cpp
// \brief  Cycle source with its own rate in simulated time
// ======================================================================
#include <Wsts/Svc/SimCycle/SimCycle.hpp>

#include <cstdio>

#include <Os/RawTime.hpp>

namespace Wsts {

SimCycle::SimCycle(const char* compName) : SimCycleComponentBase(compName) {}

SimCycle::~SimCycle() {
    m_clock.stop();
}

void SimCycle::wstsClock(double hz, const char* options) {
    (void)options;
    const bool accepted = m_clock.configure(hz, [this]() {
        if (this->isConnected_CycleOut_OutputPort(0)) {
            Os::RawTime cycleStart;
            (void)cycleStart.now();
            this->CycleOut_out(0, cycleStart);
        }
    });
    if (!accepted) {
        std::fprintf(stderr, "[WSTS] cycle source rate %g Hz is not accepted\n", hz);
        return;
    }
    m_clock.start();
}

}  // namespace Wsts
