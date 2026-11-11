#ifndef LINUXDEMO_TOPOLOGY_DEFS_HPP
#define LINUXDEMO_TOPOLOGY_DEFS_HPP

#include <cstring>

#include "LinuxDemo/Top/FppConstantsAc.hpp"
#include "Svc/Subtopologies/CdhCore/PingEntries.hpp"
#include "Svc/Subtopologies/CdhCore/SubtopologyTopologyDefs.hpp"
#include "Svc/Subtopologies/ComCcsds/PingEntries.hpp"
#include "Svc/Subtopologies/ComCcsds/SubtopologyTopologyDefs.hpp"

namespace PingEntries {
namespace LinuxDemo_rateGroup10Hz {
enum { WARN = 3, FATAL = 5 };
}
namespace LinuxDemo_rateGroup1Hz {
enum { WARN = 3, FATAL = 5 };
}
}  // namespace PingEntries

namespace LinuxDemo {

//! State handed to the topology at set-up
struct TopologyState {
    const char* hostname;                 //!< Ground system host for TCP communication
    U16 port;                             //!< Ground system port
    CdhCore::SubtopologyState cdhCore;
    ComCcsds::SubtopologyState comCcsds;
};

namespace PingEntries = ::PingEntries;

}  // namespace LinuxDemo

#endif
