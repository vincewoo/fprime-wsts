// ======================================================================
// VxDemo entry points, called from the VxWorks start-up script:
//
//   ld < VxDemo.out
//   vxDemoStart "192.168.1.10", 50000
// ======================================================================
#include <VxDemo/Top/VxDemoTopology.hpp>

#ifdef TGT_OS_TYPE_VXWORKS
#include <vxWorks.h>
#include <sysLib.h>
#endif

#include <Fw/Logger/Logger.hpp>
#include <Os/Os.hpp>

namespace {
VxDemo::TopologyState s_state{};
}

extern "C" int vxDemoStart(const char* hostname, int port) {
    Os::init();
#ifdef TGT_OS_TYPE_VXWORKS
    // Rate groups are driven by a watchdog timer counted in system clock ticks
    (void)sysClkRateSet(60);
#endif
    s_state.hostname = hostname;
    s_state.port = static_cast<U16>(port);
    VxDemo::setupTopology(s_state);
    // Returns at once: the watchdog timer fires the rate groups from then on
    VxDemo::startRateGroups();
    Fw::Logger::log("VxDemo started\n");
    return 0;
}

extern "C" void vxDemoStop() {
    VxDemo::stopRateGroups();
    VxDemo::teardownTopology(s_state);
}
