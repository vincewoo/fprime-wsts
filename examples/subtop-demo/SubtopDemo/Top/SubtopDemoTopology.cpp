// ======================================================================
// SubtopDemo topology set-up for the VxWorks flight computer.
//
// The serial driver is opened and started by phase code of the SensorLink subtopology,
// not here. The simulated deployment builds this file unchanged.
// ======================================================================
#include <SubtopDemo/Top/SubtopDemoTopology.hpp>
#include <SubtopDemo/Top/SubtopDemoTopologyAc.hpp>

using namespace SubtopDemo;

namespace {

// 10 Hz base tick divided into the 10 Hz and 1 Hz rate groups
Svc::RateGroupDriver::DividerSet rateGroupDivisors{{{1, 0}, {10, 0}}};
Svc::ActiveRateGroup::ContextArray rateGroup10HzContext(0);
Svc::ActiveRateGroup::ContextArray rateGroup1HzContext(0);

enum TopologyConstants {
    COMM_PRIORITY = 34,
    TIMER_TICKS = 6,  // 60 Hz system clock / 6 = 10 Hz
};

}  // namespace

namespace SubtopDemo {

void setupTopology(const TopologyState& state) {
    initComponents(state);
    setBaseIds();
    connectComponents();
    configComponents(state);
    if (state.hostname != nullptr && state.port != 0) {
        comDriver.configure(state.hostname, state.port);
    }
    rateGroupDriver.configure(rateGroupDivisors);
    rateGroup10Hz.configure(rateGroup10HzContext);
    rateGroup1Hz.configure(rateGroup1HzContext);

    regCommands();
    loadParameters();
    startTasks(state);

    if (state.hostname != nullptr && state.port != 0) {
        Os::TaskString name("ReceiveTask");
        comDriver.start(name, COMM_PRIORITY, Default::STACK_SIZE);
    }
}

void startRateGroups() {
    vxTimer.startTimerTick(TIMER_TICKS);
}

void stopRateGroups() {
    vxTimer.quit();
}

void teardownTopology(const TopologyState& state) {
    stopTasks(state);
    freeThreads(state);

    comDriver.stop();
    (void)comDriver.join();

    tearDownComponents(state);
}

}  // namespace SubtopDemo
