// ======================================================================
// \title  SimRawTime.hpp
// \brief  The Os::RawTime of a simulated deployment: the host's clock, or simulated time
//
// Chosen by a simulated deployment in place of Os_RawTime_Posix (CHOOSES_IMPLEMENTATIONS
// Wsts_Os_RawTime). In real time it is that implementation. In stepped time now() gives
// simulated time, which stands still within a tick: a rate group that times its cycle
// measures zero, in every run, instead of how busy the host happened to be.
// ======================================================================
#ifndef WSTS_OS_SIMRAWTIME_HPP
#define WSTS_OS_SIMRAWTIME_HPP

#include <Os/Posix/RawTime.hpp>

namespace Wsts {

class SimRawTime : public Os::Posix::RawTime::PosixRawTime {
  public:
    SimRawTime() = default;
    explicit SimRawTime(Os::RawTimeSource source) : Os::Posix::RawTime::PosixRawTime(source) {}
    ~SimRawTime() override = default;

    Status now() override;
};

}  // namespace Wsts

#endif
