// ======================================================================
// \title  CountedQueue.cpp
// \brief  The F Prime generic priority queue, counting the messages that are outstanding
// ======================================================================
#include <Wsts/Os/CountedQueue.hpp>

#include <algorithm>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include <Wsts/Core/Activity.hpp>

namespace Wsts {

namespace {

// The queues this thread has taken a message from and not come back to. An active
// component's task has its own queue here while a handler runs. A queued component is
// dispatched on its caller's thread, which may be inside a handler of its own: hence a
// list and not a flag.
thread_local std::vector<const CountedQueue*> t_handling;

// Every queue, beside the queues and not in them: a queue must fit the storage F Prime
// gives it.
//
// A queue is either waited on by a task of its own (an active component: BLOCKING
// receive) or polled by whoever dispatches it (a queued component: NONBLOCKING receive,
// typically from a rate group). A message waiting on a task's queue is work under way.
// A message waiting on a polled queue is not: nothing will touch it until the next
// dispatch, which is a later tick. It counts from the moment it is taken.
struct Entry {
    std::string name;
    std::int64_t waiting = 0;   // sent, not yet received
    std::int64_t handling = 0;  // received, handler not yet returned
    bool polled = false;
};
std::mutex s_mutex;
std::map<const CountedQueue*, Entry>& entries() {
    static std::map<const CountedQueue*, Entry> all;
    return all;
}

std::string report() {
    std::lock_guard<std::mutex> lock(s_mutex);
    std::string text;
    for (const auto& item : entries()) {
        const Entry& entry = item.second;
        const std::int64_t outstanding = entry.handling + (entry.polled ? 0 : entry.waiting);
        if (outstanding > 0) {
            text += (text.empty() ? "" : ", ") + (entry.name.empty() ? "(unnamed)" : entry.name) + ": " +
                    std::to_string(outstanding);
        }
    }
    return text;
}

bool finished(const CountedQueue* queue) {
    const auto it = std::find(t_handling.begin(), t_handling.end(), queue);
    if (it == t_handling.end()) {
        return false;
    }
    t_handling.erase(it);
    return true;
}

}  // namespace

CountedQueue::CountedQueue() {
    Activity::get().setCounting();
    Activity::get().setReporter(report);
}

CountedQueue::~CountedQueue() {
    std::lock_guard<std::mutex> lock(s_mutex);
    entries().erase(this);
}

Os::QueueInterface::Status CountedQueue::create(FwEnumStoreType id,
                                                const Fw::ConstStringBase& name,
                                                FwSizeType depth,
                                                FwSizeType messageSize) {
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        entries()[this].name = name.toChar();
    }
    return Os::Generic::PriorityQueue::create(id, name, depth, messageSize);
}

Os::QueueInterface::Status CountedQueue::send(const U8* buffer,
                                              FwSizeType size,
                                              FwQueuePriorityType priority,
                                              BlockingType blockType) {
    // Counted before the message can be received: the receiver may finish it at once
    bool counted = false;
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        Entry& entry = entries()[this];
        entry.waiting++;
        counted = !entry.polled;
        if (counted) {
            Activity::get().begin();
        }
    }
    const Status status = Os::Generic::PriorityQueue::send(buffer, size, priority, blockType);
    if (status != Os::QueueInterface::Status::OP_OK) {
        // Full, or refused: nothing to handle
        std::lock_guard<std::mutex> lock(s_mutex);
        Entry& entry = entries()[this];
        entry.waiting--;
        if (counted && !entry.polled) {
            Activity::get().end();
        }
    }
    return status;
}

Os::QueueInterface::Status CountedQueue::receive(U8* destination,
                                                 FwSizeType capacity,
                                                 BlockingType blockType,
                                                 FwSizeType& actualSize,
                                                 FwQueuePriorityType& priority) {
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        Entry& entry = entries()[this];
        if (finished(this)) {
            entry.handling--;
            Activity::get().end();
        }
        if (blockType == BlockingType::NONBLOCKING && !entry.polled) {
            // First seen to be polled: what waits on it was counted as work under way
            entry.polled = true;
            for (std::int64_t i = 0; i < entry.waiting; i++) {
                Activity::get().end();
            }
        }
    }
    const Status status = Os::Generic::PriorityQueue::receive(destination, capacity, blockType, actualSize, priority);
    if (status == Os::QueueInterface::Status::OP_OK) {
        std::lock_guard<std::mutex> lock(s_mutex);
        Entry& entry = entries()[this];
        entry.waiting--;
        entry.handling++;
        if (entry.polled) {
            Activity::get().begin();  // a waiting message of a polled queue counts from here
        }
        t_handling.push_back(this);  // outstanding until this thread asks for the next one
    }
    return status;
}

}  // namespace Wsts
