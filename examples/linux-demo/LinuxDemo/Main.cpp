// ======================================================================
// LinuxDemo entry point
// ======================================================================
#include <getopt.h>
#include <signal.h>
#include <cstdlib>

#include <Fw/Logger/Logger.hpp>
#include <LinuxDemo/Top/LinuxDemoTopology.hpp>
#include <Os/Os.hpp>

static void signalHandler(int signum) {
    (void)signum;
    LinuxDemo::stopRateGroups();
}

int main(int argc, char* argv[]) {
    Os::init();
    U16 port = 0;
    char* hostname = nullptr;
    I32 option = 0;
    while ((option = getopt(argc, argv, "hp:a:")) != -1) {
        switch (option) {
            case 'a':
                hostname = optarg;
                break;
            case 'p':
                port = static_cast<U16>(atoi(optarg));
                break;
            default:
                Fw::Logger::log("Usage: %s [-a hostname] [-p port]\n", argv[0]);
                return (option == 'h') ? 0 : 1;
        }
    }
    LinuxDemo::TopologyState inputs{};
    inputs.hostname = hostname;
    inputs.port = port;

    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    LinuxDemo::setupTopology(inputs);
    LinuxDemo::startRateGroups(Fw::TimeInterval(0, 100000));  // 10 Hz base tick
    LinuxDemo::teardownTopology(inputs);
    Fw::Logger::log("Exiting...\n");
    return 0;
}
