#ifndef SENSORLINK_SUBTOPOLOGY_DEFS_HPP
#define SENSORLINK_SUBTOPOLOGY_DEFS_HPP

#include <cstring>

#include <Fw/Logger/Logger.hpp>
#include <Fw/Types/MallocAllocator.hpp>
#include <Svc/BufferManager/BufferManager.hpp>
#include "SensorLib/SensorLink/SensorLinkConfig/FppConstantsAc.hpp"

namespace SensorLink {

namespace Allocation {
//! Allocator for the subtopology's buffer manager
inline Fw::MemAllocator& memAllocator() {
    static Fw::MallocAllocator allocator;
    return allocator;
}
}  // namespace Allocation

struct SubtopologyState {
    // Empty: the SensorLink subtopology needs no external state
};

}  // namespace SensorLink

#endif
