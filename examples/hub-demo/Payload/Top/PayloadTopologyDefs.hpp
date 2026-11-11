#ifndef PAYLOAD_TOPOLOGY_DEFS_HPP
#define PAYLOAD_TOPOLOGY_DEFS_HPP

#include "Payload/Top/FppConstantsAc.hpp"

namespace Payload {

//! State handed to the topology at set-up
struct TopologyState {
    const char* hubHostname;  //!< Address of the Cdh computer
    U16 hubPort;              //!< Port the Cdh computer's hub listens on
};

}  // namespace Payload

#endif
