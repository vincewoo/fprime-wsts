// ======================================================================
// \title  DefaultSimRawTime.cpp
// \brief  Makes Wsts::SimRawTime the Os::RawTime of a deployment that chooses Wsts_Os_RawTime
// ======================================================================
#include <Os/Delegate.hpp>
#include <Os/RawTime.hpp>
#include <Wsts/Os/SimRawTime.hpp>

namespace Os {
#if WSTS_HAS_RAWTIME_SOURCE
RawTimeInterface* RawTimeInterface::getDelegate(RawTimeHandleStorage& aligned_new_memory,
                                                const RawTimeInterface* to_copy,
                                                RawTimeSource source) {
    return Os::Delegate::makeDelegate<RawTimeInterface, Wsts::SimRawTime, RawTimeHandleStorage>(aligned_new_memory,
                                                                                               to_copy, source);
}
#else
RawTimeInterface* RawTimeInterface::getDelegate(RawTimeHandleStorage& aligned_new_memory,
                                                const RawTimeInterface* to_copy) {
    return Os::Delegate::makeDelegate<RawTimeInterface, Wsts::SimRawTime, RawTimeHandleStorage>(aligned_new_memory,
                                                                                               to_copy);
}
#endif
}  // namespace Os
