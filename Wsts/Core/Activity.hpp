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
#include <thread>
#include <vector>

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

    // ---- One at a time (stepped time) ----
    //
    // In stepped time flight code runs on one thread at a time, so that what a tick does
    // happens in the same order in every run. A thread takes the turn before it runs
    // flight code and gives it up afterwards. When the turn is free it goes to the
    // contender of lowest rank that is ready: threads of the simulation (a tick, a
    // delivery) before tasks, and tasks in the order their queues were created.

    using Ready = std::function<bool()>;
    static constexpr std::uint64_t SIMULATION_RANK = 1;  //!< a thread of the simulation
    static constexpr std::uint64_t TASK_START_RANK = 1000;  //!< a task that starts, plus its order among the tasks
    static constexpr std::uint64_t TASK_RANK = 1000000;  //!< a task at its queue, plus the index of the queue

    bool exclusive() const { return m_exclusive.load(); }
    void setExclusive(bool exclusive);
    //! Take the turn (again, if this thread has it: it is counted). False, and nothing
    //! taken, when flight code is not run one at a time.
    bool enter(std::uint64_t rank = SIMULATION_RANK);
    //! Give up what enter() or awaitTurn() took
    void leave();
    //! For a task between two messages: give up the turn if this thread has it, and wait
    //! until `ready` says there is something to do and it is this contender's turn. Both
    //! in one step: who runs next does not depend on how quickly this thread comes back.
    //! `ready` is called with the turn's lock held and must not call back into the turn.
    //! False when flight code is no longer run one at a time (nothing is held then).
    bool awaitTurn(std::uint64_t rank, const Ready& ready);
    //! While set, no task is given the turn: the deployment's start-up code runs alone on
    //! its own thread, and the tasks it starts begin together, in their order, once the
    //! first tick is asked for. Only while flight code runs one at a time.
    void holdTasks(bool hold);
    bool holdingTasks() const { return m_holdTasks.load(); }
    //! A task of this rank (TASK_START_RANK plus its order) has been started, on the thread
    //! that starts it, and its own thread is not there yet. Until that thread first takes
    //! the turn, no contender of a higher rank is given it: the order in which tasks begin
    //! does not depend on how quickly the host schedules their threads.
    void expectTask(std::uint64_t rank);
    //! The task will not come after all (its thread could not be started)
    void unexpectTask(std::uint64_t rank);
    //! Start-up has settled: every task that was started has come to its queue (or is held
    //! there by holdTasks()), and no one has the turn. With nothing outstanding as well,
    //! the deployment is in the same state in every run.
    bool tasksSettled();
    //! Give up the turn however often this thread took it (a task that ends inside its
    //! last handler never comes back to give it up)
    void abandon();
    //! Something a contender waits for may have changed (a message was sent)
    void poke();
    //! Whether this thread has the turn
    bool hasTurn() const;

    //! Back to the initial state (unit tests)
    void reset();

    //! Work done on a thread of the simulation for as long as this lives
    class Scope {
      public:
        Scope();
        ~Scope();
        Scope(const Scope&) = delete;
        Scope& operator=(const Scope&) = delete;

      private:
        bool m_entered;  //!< this scope took the turn (flight code runs one at a time)
    };

  private:
    Activity() = default;

    std::atomic<std::uint64_t> m_pending{0};
    std::atomic<std::uint64_t> m_total{0};
    std::atomic<bool> m_counting{false};
    std::mutex m_mutex;
    std::condition_variable m_quiet;
    Reporter m_reporter;

    struct Contender {
        std::uint64_t rank;
        const Ready* ready;  //!< null: ready as soon as the turn is free
    };
    void releaseLocked();
    bool grantedLocked(const Contender* self);
    std::atomic<bool> m_exclusive{false};
    std::atomic<bool> m_holdTasks{false};
    std::mutex m_turnMutex;  //!< the turn: all below
    std::condition_variable m_turn;
    std::thread::id m_owner;
    std::uint32_t m_depth = 0;
    std::vector<const Contender*> m_contenders;
    std::vector<std::uint64_t> m_expected;  //!< ranks of tasks started whose thread has not taken the turn yet
};

}  // namespace Wsts

#endif
