#ifndef SENSORSIM_SUBTOPOLOGY_DEFS_HPP
#define SENSORSIM_SUBTOPOLOGY_DEFS_HPP

// Needed by the phase code of the instances in SensorSim.fpp. The flight deployment's
// topology definitions know nothing of this file: the manifest lists it under
// `simulators.headers`.
#include <cstdlib>
#include <cstring>

#include <Fw/Types/MallocAllocator.hpp>
#include <Svc/BufferManager/BufferManager.hpp>
#include <Svc/PassiveRateGroup/PassiveRateGroup.hpp>

namespace SensorSim {

enum Config {
    TX_BUFFER_SIZE = 64,
    TX_BUFFER_COUNT = 4,
    TX_BUFFER_MANAGER_ID = 700,
    //! Rate of fastGroup: the same number as `clocks` in the manifest gives simClock
    FAST_GROUP_HZ = 125,
};

namespace Allocation {
//! Allocator for the simulator's transmit buffers
inline Fw::MemAllocator& memAllocator() {
    static Fw::MallocAllocator allocator;
    return allocator;
}
}  // namespace Allocation

}  // namespace SensorSim

#endif
