#ifndef CDH_TOPOLOGY_HPP
#define CDH_TOPOLOGY_HPP

#include <Cdh/Top/CdhTopologyDefs.hpp>

namespace Cdh {

void setupTopology(const TopologyState& state);
//! Start the watchdog timer that cycles the rate groups. Returns at once.
void startRateGroups();
void stopRateGroups();
void teardownTopology(const TopologyState& state);

}  // namespace Cdh

#endif
