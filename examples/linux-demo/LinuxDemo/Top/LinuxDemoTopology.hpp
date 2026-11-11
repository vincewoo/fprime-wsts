#ifndef LINUXDEMO_TOPOLOGY_HPP
#define LINUXDEMO_TOPOLOGY_HPP

#include <Fw/Time/TimeInterval.hpp>
#include <LinuxDemo/Top/LinuxDemoTopologyDefs.hpp>

namespace LinuxDemo {

void setupTopology(const TopologyState& state);
//! Cycle the rate groups. Blocks until stopRateGroups() is called.
void startRateGroups(const Fw::TimeInterval& interval);
void stopRateGroups();
void teardownTopology(const TopologyState& state);

}  // namespace LinuxDemo

#endif
