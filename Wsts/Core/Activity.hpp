// ======================================================================
// \title  Activity.hpp
// \brief  Count of the work flight software has been given and not yet finished
//
// The basis of stepped time (docs/stepped-time.md): a tick is finished when this count is
// back to zero. A piece of work is a message on a component's queue, from the moment it is
// sent until its handler has returned, or anything a thread of the simulation does on
// flight software's behalf outside a queue (delivering injected bytes, issuing a cycle),
// for as long as an Activity::Scope is held.
//
// Plain C++, no F Prime dependencies. The queue that counts is Wsts/Os.
// ======================================================================
#ifndef WSTS_CORE_ACTIVITY_HPP
#define WSTS_CORE_ACTIVITY_HPP

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

namespace Wsts {

class Activity {
  public:
    static Activity& get();

    //! One more piece of work is outstanding
    void begin();
    //! One piece of work is finished
    void end();

    //! Work outstanding now
    std::uint64_t pending() const { return m_pending.load(); }
    //! Work outstanding that is not this thread's own scopes
    std::uint64_t pendingElsewhere() const;
    //! Pieces of work begun since start-up
    std::uint64_t total() const { return m_total.load(); }
    //! Whether anything counts at all: false until the counting queue has been used, as in
    //! a deployment built with another queue implementation
    bool counting() const { return m_counting.load(); }
    void setCounting() { m_counting.store(true); }

    //! Block until no work is outstanding, or the time is up. True when quiescent.
    bool waitQuiescent(std::chrono::milliseconds timeout);

    //! Who can say where the outstanding work is: the counting queue, by queue name
    using Reporter = std::function<std::string()>;
    void setReporter(Reporter reporter);
    //! Where the outstanding work is, as "<queue>: <messages>, ...". Empty when nobody can say.
    std::string holders();

    //! Back to the initial state (unit tests)
    void reset();

    //! Work done on a thread of the simulation for as long as this lives
    class Scope {
      public:
        Scope();
        ~Scope();
        Scope(const Scope&) = delete;
        Scope& operator=(const Scope&) = delete;
    };

  private:
    Activity() = default;

    std::atomic<std::uint64_t> m_pending{0};
    std::atomic<std::uint64_t> m_total{0};
    std::atomic<bool> m_counting{false};
    std::mutex m_mutex;
    std::condition_variable m_quiet;
    Reporter m_reporter;
};

}  // namespace Wsts

#endif
