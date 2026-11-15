// ======================================================================
// \title  DefaultCountedQueue.cpp
// \brief  Makes Wsts::CountedQueue the Os::Queue of a deployment that chooses Wsts_Os_Queue
// ======================================================================
#include <Os/Delegate.hpp>
#include <Os/Queue.hpp>
#include <Wsts/Os/CountedQueue.hpp>

namespace Os {
QueueInterface* QueueInterface::getDelegate(QueueHandleStorage& aligned_new_memory) {
    return Os::Delegate::makeDelegate<QueueInterface, Wsts::CountedQueue, QueueHandleStorage>(aligned_new_memory);
}
}  // namespace Os
