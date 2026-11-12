// ======================================================================
// Cdh topology set-up: the command and data handling computer, on VxWorks.
//
// It talks to the ground and reaches the payload computer through an F Prime hub over
// Ethernet. The serial sensor's driver is opened and started by phase code of the
// SensorLink subtopology, not here. Everything in this file is ordinary flight code; the
// simulated deployment builds it unchanged.
// ======================================================================
#include <Cdh/Top/CdhTopology.hpp>
#include <Cdh/Top/CdhTopologyAc.hpp>

#include <Fw/Logger/Logger.hpp>

using namespace Cdh;

namespace {

// 10 Hz base tick divided into the 10 Hz and 1 Hz rate groups
Svc::RateGroupDriver::DividerSet rateGroupDivisors{{{1, 0}, {10, 0}}};
Svc::ActiveRateGroup::ContextArray rateGroup10HzContext(0);
Svc::ActiveRateGroup::ContextArray rateGroup1HzContext(0);

enum TopologyConstants {
    COMM_PRIORITY = 34,
    HUB_PRIORITY = 33,
    TIMER_TICKS = 6,  // 60 Hz system clock / 6 = 10 Hz
    //! Commands with opcodes from here up belong to the payload computer
    PAYLOAD_BASE_OPCODE = 0x20000000,
};

}  // namespace

namespace Cdh {

void setupTopology(const TopologyState& state) {
    initComponents(state);
    setBaseIds();
    connectComponents();
    configComponents(state);

    rateGroupDriver.configure(rateGroupDivisors);
    rateGroup10Hz.configure(rateGroup10HzContext);
    rateGroup1Hz.configure(rateGroup1HzContext);
    cmdSplitter.configure(PAYLOAD_BASE_OPCODE);
    if (state.hostname != nullptr && state.port != 0) {
        comDriver.configure(state.hostname, state.port);
    }
    // The payload computer connects to this one
    if (hubDriver.configure("0.0.0.0", state.hubPort) != Drv::SOCK_SUCCESS) {
        Fw::Logger::log("[ERROR] Failed to configure the hub link\n");
    }

    regCommands();
    loadParameters();
    startTasks(state);

    Os::TaskString hubName("HubReceiveTask");
    hubDriver.start(hubName, HUB_PRIORITY, Default::STACK_SIZE);
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
    hubDriver.stop();
    (void)hubDriver.join();

    tearDownComponents(state);
}

}  // namespace Cdh
