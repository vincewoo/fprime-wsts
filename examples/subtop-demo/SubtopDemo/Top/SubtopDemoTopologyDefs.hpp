#ifndef SUBTOPDEMO_TOPOLOGY_DEFS_HPP
#define SUBTOPDEMO_TOPOLOGY_DEFS_HPP

#include "SensorLib/SensorLink/SubtopologyTopologyDefs.hpp"
#include "SubtopDemo/Top/FppConstantsAc.hpp"
#include "Svc/Subtopologies/CdhCore/PingEntries.hpp"
#include "Svc/Subtopologies/CdhCore/SubtopologyTopologyDefs.hpp"
#include "Svc/Subtopologies/ComCcsds/PingEntries.hpp"
#include "Svc/Subtopologies/ComCcsds/SubtopologyTopologyDefs.hpp"

namespace PingEntries {
namespace SubtopDemo_rateGroup10Hz {
enum { WARN = 3, FATAL = 5 };
}
namespace SubtopDemo_rateGroup1Hz {
enum { WARN = 3, FATAL = 5 };
}
}  // namespace PingEntries

namespace SubtopDemo {

//! State handed to the topology at set-up
struct TopologyState {
    const char* hostname;  //!< Ground system host for TCP communication
    U16 port;              //!< Ground system port
    CdhCore::SubtopologyState cdhCore;
    ComCcsds::SubtopologyState comCcsds;
    SensorLink::SubtopologyState sensorLink;
};

namespace PingEntries = ::PingEntries;

}  // namespace SubtopDemo

#endif
