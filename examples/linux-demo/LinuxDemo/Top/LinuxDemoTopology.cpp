// ======================================================================
// LinuxDemo topology set-up for the flight computer (a Linux single-board computer).
//
// Everything hardware specific in this file is ordinary flight code: device paths, pin
// numbers and driver calls. The simulated deployment builds this file unchanged.
// ======================================================================
#include <LinuxDemo/Top/LinuxDemoTopology.hpp>
#include <LinuxDemo/Top/LinuxDemoTopologyAc.hpp>

#include <Fw/Logger/Logger.hpp>

using namespace LinuxDemo;

namespace {

// 10 Hz base tick divided into the 10 Hz and 1 Hz rate groups
Svc::RateGroupDriver::DividerSet rateGroupDivisors{{{1, 0}, {10, 0}}};
Svc::ActiveRateGroup::ContextArray rateGroup10HzContext(0);
Svc::ActiveRateGroup::ContextArray rateGroup1HzContext(0);

enum TopologyConstants {
    COMM_PRIORITY = 34,
    UART_PRIORITY = 30,
    HEATER_PIN = 17,
    SWITCH_PIN = 27,
};

void configureTopology() {
    rateGroupDriver.configure(rateGroupDivisors);
    rateGroup10Hz.configure(rateGroup10HzContext);
    rateGroup1Hz.configure(rateGroup1HzContext);

    // IMU on I2C bus 1
    if (!imuBus.open("/dev/i2c-1")) {
        Fw::Logger::log("[ERROR] Failed to open I2C bus\n");
    }
    // Serial temperature sensor
    if (!sensorUart.open("/dev/ttyS1", Drv::PosixUartDriver::BAUD_115K, Drv::PosixUartDriver::NO_FLOW,
                         Drv::PosixUartDriver::PARITY_NONE, 1024)) {
        Fw::Logger::log("[ERROR] Failed to open sensor UART\n");
    }
    // Heater enable output and over-temperature switch input
    if (heaterPin.open("/dev/gpiochip0", HEATER_PIN, Drv::LinuxGpioDriver::GPIO_OUTPUT) != Os::File::OP_OK) {
        Fw::Logger::log("[ERROR] Failed to open heater pin\n");
    }
    if (switchPin.open("/dev/gpiochip0", SWITCH_PIN, Drv::LinuxGpioDriver::GPIO_INTERRUPT_RISING_EDGE) !=
        Os::File::OP_OK) {
        Fw::Logger::log("[ERROR] Failed to open switch pin\n");
    }
}

}  // namespace

namespace LinuxDemo {

void setupTopology(const TopologyState& state) {
    initComponents(state);
    setBaseIds();
    connectComponents();
    configComponents(state);
    if (state.hostname != nullptr && state.port != 0) {
        comDriver.configure(state.hostname, state.port);
    }
    configureTopology();
    regCommands();
    loadParameters();
    startTasks(state);

    sensorUart.start(UART_PRIORITY, Default::STACK_SIZE);
    (void)switchPin.start();
    if (state.hostname != nullptr && state.port != 0) {
        Os::TaskString name("ReceiveTask");
        comDriver.start(name, COMM_PRIORITY, Default::STACK_SIZE);
    }
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

    comDriver.stop();
    (void)comDriver.join();
    sensorUart.quitReadThread();
    (void)sensorUart.join();
    switchPin.stop();
    switchPin.join();

    tearDownComponents(state);
}

}  // namespace LinuxDemo
