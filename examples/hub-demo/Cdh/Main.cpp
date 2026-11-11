// ======================================================================
// Cdh entry point
// ======================================================================
#include <getopt.h>
#include <signal.h>
#include <cstdlib>

#include <Cdh/Top/CdhTopology.hpp>
#include <Fw/Logger/Logger.hpp>
#include <Os/Os.hpp>

static void signalHandler(int signum) {
    (void)signum;
    Cdh::stopRateGroups();
}

int main(int argc, char* argv[]) {
    Os::init();
    U16 port = 0;
    U16 hubPort = 50050;
    char* hostname = nullptr;
    I32 option = 0;
    while ((option = getopt(argc, argv, "hp:a:b:")) != -1) {
        switch (option) {
            case 'a':
                hostname = optarg;
                break;
            case 'p':
                port = static_cast<U16>(atoi(optarg));
                break;
            case 'b':
                hubPort = static_cast<U16>(atoi(optarg));
                break;
            default:
                Fw::Logger::log("Usage: %s [-a ground hostname] [-p ground port] [-b hub port]\n", argv[0]);
                return (option == 'h') ? 0 : 1;
        }
    }
    Cdh::TopologyState inputs{};
    inputs.hostname = hostname;
    inputs.port = port;
    inputs.hubPort = hubPort;

    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    Cdh::setupTopology(inputs);
    Cdh::startRateGroups(Fw::TimeInterval(0, 100000));  // 10 Hz base tick
    Cdh::teardownTopology(inputs);
    Fw::Logger::log("Exiting...\n");
    return 0;
}
