// ======================================================================
// Payload topology set-up: the payload computer.
//
// It has no ground link. Commands reach it, and its events and telemetry leave it,
// through an F Prime hub over Ethernet to the Cdh computer. Everything in this file is
// ordinary flight code; the simulated deployment builds it unchanged.
// ======================================================================
#include <Payload/Top/PayloadTopology.hpp>
#include <Payload/Top/PayloadTopologyAc.hpp>

#include <cstring>

#include <Fw/Logger/Logger.hpp>
#include <Fw/Types/MallocAllocator.hpp>

using namespace Payload;

namespace {

// 1 Hz base tick, one rate group
Svc::RateGroupDriver::DividerSet rateGroupDivisors{{{1, 0}}};
Svc::ActiveRateGroup::ContextArray rateGroup1HzContext(0);

Fw::MallocAllocator mallocator;

enum TopologyConstants {
    HUB_PRIORITY = 33,
    BUFFER_MANAGER_ID = 200,
    HUB_BUFFER_SIZE = 1024,
    HUB_BUFFER_COUNT = 40,
};

}  // namespace

namespace Payload {

void setupTopology(const TopologyState& state) {
    initComponents(state);
    setBaseIds();
    connectComponents();
    configComponents(state);

    rateGroupDriver.configure(rateGroupDivisors);
    rateGroup1Hz.configure(rateGroup1HzContext);

    Svc::BufferManager::BufferBins bins;
    (void)std::memset(&bins, 0, sizeof(bins));
    bins.bins[0].bufferSize = HUB_BUFFER_SIZE;
    bins.bins[0].numBuffers = HUB_BUFFER_COUNT;
    bufferManager.setup(BUFFER_MANAGER_ID, 0, mallocator, bins);

    // The hub link is started before anything reports: every event on this computer
    // travels through it.
    if (hubDriver.configure(state.hubHostname, state.hubPort) != Drv::SOCK_SUCCESS) {
        Fw::Logger::log("[ERROR] Failed to configure the hub link\n");
    }
    Os::TaskString hubName("HubReceiveTask");
    hubDriver.start(hubName, HUB_PRIORITY, Default::STACK_SIZE);

    regCommands();
    startTasks(state);
}

void startRateGroups(const Fw::TimeInterval& interval) {
    linuxTimer.startTimer(interval);
}

void stopRateGroups() {
    linuxTimer.quit();
}

void teardownTopology(const TopologyState& state) {
    stopTasks(state);
    freeThreads(state);

    hubDriver.stop();
    (void)hubDriver.join();

    tearDownComponents(state);
    bufferManager.cleanup();
}

}  // namespace Payload
