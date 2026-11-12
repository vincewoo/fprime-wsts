#ifndef SENSORLINK_SUBTOPOLOGY_DEFS_HPP
#define SENSORLINK_SUBTOPOLOGY_DEFS_HPP

#include <cstring>

#include <Fw/Logger/Logger.hpp>
#include <Fw/Types/MallocAllocator.hpp>
#include <Svc/BufferManager/BufferManager.hpp>
#include <VxWorks/Drv/VxWorksUartDriver/VxWorksUartDriver.hpp>
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
    //! Line speed of the sensor's serial port. The deployment may set another before set-up.
    VxWorksDrv::VxWorksUartDriver::UartBaudRate baud = VxWorksDrv::VxWorksUartDriver::BAUD_19200;
};

}  // namespace SensorLink

#endif
