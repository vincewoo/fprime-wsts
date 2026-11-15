// ======================================================================
// \title  CountedQueue.hpp
// \brief  The F Prime generic priority queue, counting the messages that are outstanding
//
// Chosen by a simulated deployment in place of Os_Generic_PriorityQueue
// (CHOOSES_IMPLEMENTATIONS Wsts_Os_Queue). It behaves exactly as that queue does; it also
// tells Wsts::Activity when a message is sent and when the handler of a received message
// has returned, which is how the simulation knows that flight software has finished what
// a tick caused (docs/stepped-time.md).
// ======================================================================
#ifndef WSTS_OS_COUNTEDQUEUE_HPP
#define WSTS_OS_COUNTEDQUEUE_HPP

#include <Os/Generic/PriorityQueue.hpp>

namespace Wsts {

class CountedQueue : public Os::Generic::PriorityQueue {
  public:
    CountedQueue();
    ~CountedQueue() override;

    //! As the generic queue; the name is kept, to say which queue holds work up
    Status create(FwEnumStoreType id, const Fw::ConstStringBase& name, FwSizeType depth, FwSizeType messageSize) override;

    //! As the generic queue; a message that was accepted is outstanding from here on
    Status send(const U8* buffer, FwSizeType size, FwQueuePriorityType priority, BlockingType blockType) override;

    //! As the generic queue. A thread that comes back for the next message has finished the
    //! one it took before: that one is no longer outstanding.
    Status receive(U8* destination,
                   FwSizeType capacity,
                   BlockingType blockType,
                   FwSizeType& actualSize,
                   FwQueuePriorityType& priority) override;
};

}  // namespace Wsts

#endif
