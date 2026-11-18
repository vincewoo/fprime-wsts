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

// F Prime 4.4 gives getDelegate() a RawTimeSource; 4.2 has none
#if defined(__has_include) && __has_include(<config/RawTimeSource.hpp>)
#define WSTS_HAS_RAWTIME_SOURCE 1
#else
#define WSTS_HAS_RAWTIME_SOURCE 0
#endif

namespace Wsts {

class SimRawTime : public Os::Posix::RawTime::PosixRawTime {
  public:
    SimRawTime() = default;
#if WSTS_HAS_RAWTIME_SOURCE
    explicit SimRawTime(Os::RawTimeSource source) : Os::Posix::RawTime::PosixRawTime(source) {}
#endif
    ~SimRawTime() override = default;

    Status now() override;
};

}  // namespace Wsts

#endif
