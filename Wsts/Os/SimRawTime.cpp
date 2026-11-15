// ======================================================================
// \title  SimRawTime.cpp
// \brief  The Os::RawTime of a simulated deployment: the host's clock, or simulated time
// ======================================================================
#include <Wsts/Os/SimRawTime.hpp>

#include <Wsts/Core/Control.hpp>

namespace Wsts {

Os::RawTimeInterface::Status SimRawTime::now() {
    Control& control = Control::get();
    if (!control.stepped()) {
        return Os::Posix::RawTime::PosixRawTime::now();
    }
    const std::uint64_t nanoseconds = control.simTimeNs();
    auto* handle = static_cast<Os::Posix::RawTime::PosixRawTimeHandle*>(this->getHandle());
    handle->m_timespec.tv_sec = static_cast<time_t>(nanoseconds / 1000000000ull);
    handle->m_timespec.tv_nsec = static_cast<long>(nanoseconds % 1000000000ull);
    return Status::OP_OK;
}

}  // namespace Wsts
