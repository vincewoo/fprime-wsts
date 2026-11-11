#ifndef CDH_TOPOLOGY_HPP
#define CDH_TOPOLOGY_HPP

#include <Cdh/Top/CdhTopologyDefs.hpp>
#include <Fw/Time/TimeInterval.hpp>

namespace Cdh {

void setupTopology(const TopologyState& state);
//! Cycle the rate groups. Blocks until stopRateGroups() is called.
void startRateGroups(const Fw::TimeInterval& interval);
void stopRateGroups();
void teardownTopology(const TopologyState& state);

}  // namespace Cdh

#endif
