#ifndef PAYLOAD_TOPOLOGY_HPP
#define PAYLOAD_TOPOLOGY_HPP

#include <Fw/Time/TimeInterval.hpp>
#include <Payload/Top/PayloadTopologyDefs.hpp>

namespace Payload {

void setupTopology(const TopologyState& state);
//! Cycle the rate groups. Blocks until stopRateGroups() is called.
void startRateGroups(const Fw::TimeInterval& interval);
void stopRateGroups();
void teardownTopology(const TopologyState& state);

}  // namespace Payload

#endif
