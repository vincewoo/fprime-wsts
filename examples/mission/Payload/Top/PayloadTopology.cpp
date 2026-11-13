// ======================================================================
// Payload topology set-up: the payload computer, a Linux single-board computer.
//
// It has no ground link. Commands reach it, and its events and telemetry leave it,
// through an F Prime hub over Ethernet to the Cdh computer. Everything hardware specific
// in this file is ordinary flight code: device paths, pin numbers and driver calls. The
// simulated deployment builds it unchanged.
// ======================================================================
#include <Payload/Top/PayloadTopology.hpp>
#include <Payload/Top/PayloadTopologyAc.hpp>

#include <cstring>

#include <Fw/Logger/Logger.hpp>
#include <Fw/Types/MallocAllocator.hpp>

// From sensor-lib: the SensorLib_Units target exports the directory this is in
#include <units/convert.h>

using namespace Payload;

namespace {

// 20 Hz base tick divided into the 10 Hz and 1 Hz rate groups
Svc::RateGroupDriver::DividerSet rateGroupDivisors{{{2, 0}, {20, 0}}};
Svc::ActiveRateGroup::ContextArray rateGroup10HzContext(0);
Svc::ActiveRateGroup::ContextArray rateGroup1HzContext(0);

Fw::MallocAllocator mallocator;

enum TopologyConstants {
    HUB_PRIORITY = 33,
    UART_PRIORITY = 30,
    BUFFER_MANAGER_ID = 200,
    BUFFER_SIZE = 1024,
    BUFFER_COUNT = 40,
    HEATER_PIN = 17,
    SWITCH_PIN = 27,
    CAMERA_EXPOSURE_MS = 20,
    CAMERA_TICK_HZ = 48000,
};

void configureDevices() {
    // IMU and power monitor on I2C bus 1
    if (!i2cBus.open("/dev/i2c-1")) {
        Fw::Logger::log("[ERROR] Failed to open I2C bus\n");
    }
    // Serial temperature sensor
    if (!sensorUart.open("/dev/ttyS1", Drv::PosixUartDriver::BAUD_115K, Drv::PosixUartDriver::NO_FLOW,
                         Drv::PosixUartDriver::PARITY_NONE, BUFFER_SIZE)) {
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
    // Instrument converter on SPI bus 0, chip select 0
    if (!spiBus.open(0, 0, Drv::SPI_FREQUENCY_1MHZ)) {
        Fw::Logger::log("[ERROR] Failed to open SPI bus\n");
    }
    // Registers of the FPGA's housekeeping block
    fpgaRegs.configure("/dev/uio0");
}

}  // namespace

namespace Payload {

void setupTopology(const TopologyState& state) {
    initComponents(state);
    setBaseIds();
    connectComponents();
    configComponents(state);

    rateGroupDriver.configure(rateGroupDivisors);
    rateGroup10Hz.configure(rateGroup10HzContext);
    rateGroup1Hz.configure(rateGroup1HzContext);

    Svc::BufferManager::BufferBins bins;
    (void)std::memset(&bins, 0, sizeof(bins));
    bins.bins[0].bufferSize = BUFFER_SIZE;
    bins.bins[0].numBuffers = BUFFER_COUNT;
    bufferManager.setup(BUFFER_MANAGER_ID, 0, mallocator, bins);

    // The hub link is started before anything reports: every event on this computer
    // travels through it.
    if (hubDriver.configure(state.hubHostname, state.hubPort) != Drv::SOCK_SUCCESS) {
        Fw::Logger::log("[ERROR] Failed to configure the hub link\n");
    }
    Os::TaskString hubName("HubReceiveTask");
    hubDriver.start(hubName, HUB_PRIORITY, Default::STACK_SIZE);

    configureDevices();
    imager.configure(Units::millisToTicks(CAMERA_EXPOSURE_MS, CAMERA_TICK_HZ));
    regCommands();
    startTasks(state);

    sensorUart.start(UART_PRIORITY, Default::STACK_SIZE);
    (void)switchPin.start();
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

    sensorUart.quitReadThread();
    (void)sensorUart.join();
    switchPin.stop();
    switchPin.join();
    hubDriver.stop();
    (void)hubDriver.join();

    tearDownComponents(state);
    bufferManager.cleanup();
}

}  // namespace Payload
