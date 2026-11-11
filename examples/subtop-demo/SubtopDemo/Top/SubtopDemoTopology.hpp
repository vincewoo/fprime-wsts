#ifndef SUBTOPDEMO_TOPOLOGY_HPP
#define SUBTOPDEMO_TOPOLOGY_HPP

#include <SubtopDemo/Top/SubtopDemoTopologyDefs.hpp>

namespace SubtopDemo {

void setupTopology(const TopologyState& state);
//! Start the watchdog timer that cycles the rate groups. Returns immediately.
void startRateGroups();
void stopRateGroups();
void teardownTopology(const TopologyState& state);

}  // namespace SubtopDemo

#endif
