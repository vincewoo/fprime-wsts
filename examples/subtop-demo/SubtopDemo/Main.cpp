// ======================================================================
// SubtopDemo entry points.
//
// On the flight computer the image is a downloadable kernel module and subtopDemoStart()
// is called from the VxWorks start-up script. main() is for native builds.
// ======================================================================
#include <getopt.h>
#include <signal.h>
#include <unistd.h>
#include <cstdlib>

#include <Fw/Logger/Logger.hpp>
#include <Os/Os.hpp>
#include <SubtopDemo/Top/SubtopDemoTopology.hpp>

#ifdef TGT_OS_TYPE_VXWORKS
#include <vxWorks.h>
#include <sysLib.h>
#endif

namespace {
SubtopDemo::TopologyState s_state{};
volatile sig_atomic_t s_stop = 0;

void signalHandler(int signum) {
    (void)signum;
    s_stop = 1;
}
}  // namespace

extern "C" int subtopDemoStart(const char* hostname, int port) {
    Os::init();
#ifdef TGT_OS_TYPE_VXWORKS
    // Rate groups are driven by a watchdog timer counted in system clock ticks
    (void)sysClkRateSet(60);
#endif
    s_state.hostname = hostname;
    s_state.port = static_cast<U16>(port);
    SubtopDemo::setupTopology(s_state);
    // Returns at once: the watchdog timer fires the rate groups from then on
    SubtopDemo::startRateGroups();
    Fw::Logger::log("SubtopDemo started\n");
    return 0;
}

extern "C" void subtopDemoStop() {
    SubtopDemo::stopRateGroups();
    SubtopDemo::teardownTopology(s_state);
}

int main(int argc, char* argv[]) {
    const char* hostname = nullptr;
    int port = 0;
    int option = 0;
    while ((option = getopt(argc, argv, "hp:a:")) != -1) {
        switch (option) {
            case 'a':
                hostname = optarg;
                break;
            case 'p':
                port = atoi(optarg);
                break;
            default:
                Fw::Logger::log("Usage: %s [-a hostname] [-p port]\n", argv[0]);
                return (option == 'h') ? 0 : 1;
        }
    }
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    (void)subtopDemoStart(hostname, port);
    while (!s_stop) {
        (void)usleep(100000);
    }
    subtopDemoStop();
    return 0;
}
