#ifndef VXDEMO_TOPOLOGY_HPP
#define VXDEMO_TOPOLOGY_HPP

#include <VxDemo/Top/VxDemoTopologyDefs.hpp>

namespace VxDemo {

void setupTopology(const TopologyState& state);
//! Start the watchdog timer that cycles the rate groups. Returns immediately.
void startRateGroups();
void stopRateGroups();
void teardownTopology(const TopologyState& state);

}  // namespace VxDemo

#endif
