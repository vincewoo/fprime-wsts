// ======================================================================
// Payload entry point
// ======================================================================
#include <getopt.h>
#include <signal.h>
#include <cstdlib>

#include <Fw/Logger/Logger.hpp>
#include <Os/Os.hpp>
#include <Payload/Top/PayloadTopology.hpp>

#ifndef MISSION_VEHICLE_ID
#error "MISSION_VEHICLE_ID is not defined: the project's CMakeLists.txt sets it for every module"
#endif

static void signalHandler(int signum) {
    (void)signum;
    Payload::stopRateGroups();
}

int main(int argc, char* argv[]) {
    Os::init();
    U16 hubPort = 50050;
    const char* hubHostname = "192.168.10.1";  // the Cdh computer on the spacecraft network
    I32 option = 0;
    while ((option = getopt(argc, argv, "hp:a:")) != -1) {
        switch (option) {
            case 'a':
                hubHostname = optarg;
                break;
            case 'p':
                hubPort = static_cast<U16>(atoi(optarg));
                break;
            default:
                Fw::Logger::log("Usage: %s [-a Cdh hostname] [-p Cdh hub port]\n", argv[0]);
                return (option == 'h') ? 0 : 1;
        }
    }
    Payload::TopologyState inputs{};
    inputs.hubHostname = hubHostname;
    inputs.hubPort = hubPort;

    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    Payload::setupTopology(inputs);
    Payload::startRateGroups(Fw::TimeInterval(0, 50000));  // 20 Hz base tick
    Payload::teardownTopology(inputs);
    Fw::Logger::log("Exiting...\n");
    return 0;
}
