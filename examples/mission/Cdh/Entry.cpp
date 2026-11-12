// ======================================================================
// Cdh entry points, called from the VxWorks start-up script:
//
//   ld < Cdh.out
//   cdhStart "192.168.1.10", 50000, 50050
// ======================================================================
#include <Cdh/Top/CdhTopology.hpp>

#ifdef TGT_OS_TYPE_VXWORKS
#include <vxWorks.h>
#include <sysLib.h>
#endif

#include <Fw/Logger/Logger.hpp>
#include <Os/Os.hpp>

namespace {
Cdh::TopologyState s_state{};
}

extern "C" int cdhStart(const char* hostname, int port, int hubPort) {
    Os::init();
#ifdef TGT_OS_TYPE_VXWORKS
    // Rate groups are driven by a watchdog timer counted in system clock ticks
    (void)sysClkRateSet(60);
#endif
    s_state.hostname = hostname;
    s_state.port = static_cast<U16>(port);
    s_state.hubPort = static_cast<U16>(hubPort);
    Cdh::setupTopology(s_state);
    // Returns at once: the watchdog timer fires the rate groups from then on
    Cdh::startRateGroups();
    Fw::Logger::log("Cdh started\n");
    return 0;
}

extern "C" void cdhStop() {
    Cdh::stopRateGroups();
    Cdh::teardownTopology(s_state);
}
