// ======================================================================
// VxDemo topology set-up for the VxWorks flight computer.
//
// The simulated deployment builds this file unchanged.
// ======================================================================
#include <VxDemo/Top/VxDemoTopology.hpp>
#include <VxDemo/Top/VxDemoTopologyAc.hpp>

#include <Fw/Logger/Logger.hpp>

using namespace VxDemo;

namespace {

// 10 Hz base tick divided into the 10 Hz and 1 Hz rate groups
Svc::RateGroupDriver::DividerSet rateGroupDivisors{{{1, 0}, {10, 0}}};
Svc::ActiveRateGroup::ContextArray rateGroup10HzContext(0);
Svc::ActiveRateGroup::ContextArray rateGroup1HzContext(0);

enum TopologyConstants {
    COMM_PRIORITY = 34,
    UART_PRIORITY = 30,
    TIMER_TICKS = 6,  // 60 Hz system clock / 6 = 10 Hz
};

}  // namespace

namespace VxDemo {

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

    // Temperature sensor on the second on-board serial port
    if (!sensorUart.open("/tyCo/1", VxWorksDrv::VxWorksUartDriver::BAUD_115K, VxWorksDrv::VxWorksUartDriver::NO_FLOW,
                         VxWorksDrv::VxWorksUartDriver::PARITY_NONE, 1024)) {
        Fw::Logger::log("[ERROR] Failed to open sensor UART\n");
    }

    regCommands();
    loadParameters();
    startTasks(state);

    sensorUart.start(UART_PRIORITY, Default::STACK_SIZE);
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
    sensorUart.quitReadThread();
    (void)sensorUart.join();

    tearDownComponents(state);
}

}  // namespace VxDemo
