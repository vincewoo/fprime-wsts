#ifndef CDH_TOPOLOGY_DEFS_HPP
#define CDH_TOPOLOGY_DEFS_HPP

#include "Cdh/Top/FppConstantsAc.hpp"
#include "SensorLib/SensorLink/SubtopologyTopologyDefs.hpp"
#include "Svc/Subtopologies/CdhCore/PingEntries.hpp"
#include "Svc/Subtopologies/CdhCore/SubtopologyTopologyDefs.hpp"
#include "Svc/Subtopologies/ComCcsds/PingEntries.hpp"
#include "Svc/Subtopologies/ComCcsds/SubtopologyTopologyDefs.hpp"

namespace PingEntries {
namespace Cdh_rateGroup10Hz {
enum { WARN = 3, FATAL = 5 };
}
namespace Cdh_rateGroup1Hz {
enum { WARN = 3, FATAL = 5 };
}
}  // namespace PingEntries

namespace Cdh {

//! State handed to the topology at set-up
struct TopologyState {
    const char* hostname;  //!< Ground system host for TCP communication
    U16 port;              //!< Ground system port
    U16 hubPort;           //!< Port the payload computer connects to
    CdhCore::SubtopologyState cdhCore;
    ComCcsds::SubtopologyState comCcsds;
    SensorLink::SubtopologyState sensorLink;
};

namespace PingEntries = ::PingEntries;

}  // namespace Cdh

#endif
