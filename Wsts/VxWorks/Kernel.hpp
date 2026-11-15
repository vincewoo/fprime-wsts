// ======================================================================
// \title  Kernel.hpp
// \brief  The kernel behind the VxWorks API shim (docs/vxworks-shim.md)
//
// Tasks, waiting and the system clock, shared by the libraries of the shim. Internal: the
// API is the headers in include/.
//
// Waiting. Every call that blocks queues a Waiter on its object (or on nothing, for a
// delay). A give or a send hands the object to the first waiter itself, as the kernel
// does, so who gets it never depends on which thread the host runs first. The system
// clock times waiters out. Whatever wakes a waiter counts a piece of work for it
// (Activity::begin), which the woken thread holds until it blocks again: in stepped time a
// tick is not finished while a task it woke is still at work.
//
// In stepped time a blocked thread waits for its turn (Activity::awaitTurn) with a rank of
// its own: tasks of the shim after flight software's own tasks, by priority and then in
// the order they were spawned. In real time it waits on a condition variable.
//
// Locks: the kernel's mutex is never held while a call takes the turn's mutex (the turn
// asks the waiters whether they are ready, under its own mutex, and they take the
// kernel's). So the turn is never touched with the kernel locked, and waking is announced
// (notify()) only after the kernel is unlocked. The clock's own lock is never taken with
// the kernel's held.
// ======================================================================
#ifndef WSTS_VXWORKS_KERNEL_HPP
#define WSTS_VXWORKS_KERNEL_HPP

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include <Wsts/Core/Clock.hpp>

#include <msgQLib.h>
#include <semLib.h>
#include <taskLib.h>
#include <wdLib.h>

namespace Os {
class Task;
}

namespace Wsts {
namespace VxWorks {

struct Waiter;
using WaitQueue = std::vector<Waiter*>;

struct TaskRecord {
    TASK_ID id = TASK_ID_NULL;
    std::string name;
    int priority = 100;
    int options = 0;
    FUNCPTR entry = nullptr;
    _Vx_usr_arg_t args[10] = {};
    std::uint64_t order = 0;      //!< among the tasks of the shim, as they were created
    bool adopted = false;         //!< a thread the shim did not start (flight software's, main)
    bool activated = false;
    bool finished = false;
    bool suspended = false;
    bool deleteRequested = false;
    Waiter* waiting = nullptr;    //!< where it is blocked, if it is
    Os::Task* task = nullptr;
};

struct Waiter {
    enum Outcome : std::uint8_t {
        WAITING,    //!< not woken yet
        GRANTED,    //!< it has what it waited for (or, for a delay or a suspension, it is over)
        TIMED_OUT,  //!< the system clock reached its deadline
        DELETED,    //!< the object was deleted
        ENDED,      //!< its task was deleted
    };
    TaskRecord* task = nullptr;
    std::uint64_t rank = 0;
    std::uint64_t arrival = 0;
    WaitQueue* queue = nullptr;   //!< the object's queue, while it is in it
    bool timed = false;
    std::uint64_t deadline = 0;   //!< system clock ticks
    bool suspension = false;      //!< a task waiting to be resumed
    Outcome outcome = WAITING;
    bool counted = false;         //!< a piece of work was begun for it when it was woken
    std::vector<char> message;    //!< message queues: what it sends, or what it received
    bool urgent = false;
};

class Kernel {
  public:
    static Kernel& get();

    //! Rank of a task of the shim in stepped time: after flight software's tasks, by
    //! priority (0 first), then in the order the tasks were created
    static constexpr std::uint64_t RANK_BASE = 2000000;
    static std::uint64_t rankOf(const TaskRecord& task);

    std::mutex& mutex() { return m_mutex; }

    // ---- With the kernel locked ----

    //! The calling thread's task. A thread the shim did not start is adopted on first use.
    TaskRecord* self();
    //! A task by its ID (TASK_ID_NULL: the calling one); null when there is none
    TaskRecord* find(TASK_ID id);
    //! Put a waiter in an object's queue: by priority, then in order of arrival; or in order
    //! of arrival alone
    void enqueue(WaitQueue& queue, Waiter& waiter, bool byPriority);
    //! Wake a waiter: take it out of its queue and give it its outcome. Call notify() once
    //! the kernel is unlocked.
    void wake(Waiter& waiter, Waiter::Outcome outcome);
    //! Block the calling thread until the waiter is woken (or its timeout passes: NO_WAIT is
    //! not for here). The waiter is filled in and, for an object, already in its queue.
    //! `hadTurn`: whether the thread had the turn when it came in (Activity::hasTurn(),
    //! asked before the kernel was locked). Returns with the kernel locked.
    Waiter::Outcome block(std::unique_lock<std::mutex>& lock, Waiter& waiter, _Vx_ticks_t timeout, bool hadTurn);
    //! At the start of a call that may block, and after it has blocked: a task that was
    //! deleted ends here, and one that was suspended waits to be resumed
    void checkpoint(std::unique_lock<std::mutex>& lock, bool hadTurn);
    //! System clock ticks counted so far
    std::uint64_t ticks() const { return m_ticks; }

    // Objects that exist: an ID is checked against these before it is used
    std::set<const void*> live;
    std::vector<wdog*> watchdogs;
    std::uint64_t armed = 0;  //!< watchdogs started so far: the order of two due at one tick

    // ---- With the kernel unlocked ----

    //! Tell waiters that something changed
    void notify();
    //! Start the system clock if it is not running. Before any call that waits for it.
    void ensureClock();
    int clockRate() const { return m_rate.load(); }
    //! System clock ticks granted in real time and not yet issued
    std::size_t clockPending() { return m_clock.pending(); }
    bool setClockRate(int ticksPerSecond);

    //! Create (and with `activate`, start) a task
    TASK_ID create(const char* name, int priority, int options, FUNCPTR entry, const _Vx_usr_arg_t* args,
                   bool activate);
    STATUS activate(TASK_ID id);
    //! End the calling task here. Does not return.
    [[noreturn]] void exitTask();

    //! Interrupt level: watchdog routines, and what intLock() keeps them from
    std::recursive_mutex intLock;

    // ---- With the kernel locked (taskLib) ----

    //! Fill in who waits: the calling task, its rank, its place in the order of arrival
    void prepare(Waiter& waiter);
    //! Suspend or resume a task that is not the calling one
    void suspend(TaskRecord& task);
    void resume(TaskRecord& task);
    //! Delete a task that is not the calling one: it ends at its next call into the shim
    void requestDelete(TaskRecord& task);
    //! A task that has not finished, by name; null when there is none
    TaskRecord* byName(const std::string& name);

  private:
    Kernel() = default;
    bool readyLocked(const Waiter& waiter) const;
    void onClockTick();
    static void taskMain(void* argument);

    std::mutex m_mutex;
    std::condition_variable m_changed;
    std::map<TASK_ID, TaskRecord*> m_tasks;
    TASK_ID m_nextId = 0x10000;
    std::uint64_t m_created = 0;
    std::uint64_t m_arrivals = 0;
    std::vector<Waiter*> m_timed;  //!< waiters with a deadline
    std::uint64_t m_ticks = 0;

    std::mutex m_clockMutex;  //!< the clock's configuration
    CycleClock m_clock;
    //! Read without the lock first: a watchdog routine, on the clock's thread, asks while
    //! sysClkRateSet() holds the lock and waits for that thread
    std::atomic<bool> m_clockStarted{false};
    std::atomic<int> m_rate{60};
};

//! While set, the calling thread runs at interrupt level (a watchdog routine)
bool interruptLevel();
void setInterruptLevel(bool on);

}  // namespace VxWorks
}  // namespace Wsts

// The kernel objects behind the opaque IDs of the headers
struct semaphore {
    enum Kind : std::uint8_t { BINARY, COUNTING, MUTEX };
    Kind kind = BINARY;
    int options = 0;
    int count = 0;
    Wsts::VxWorks::TaskRecord* owner = nullptr;  //!< mutex: the task that has it
    int depth = 0;                               //!< mutex: how often its owner took it
    Wsts::VxWorks::WaitQueue waiters;
};

struct msg_q {
    std::size_t maxMsgs = 0;
    std::size_t maxLength = 0;
    int options = 0;
    std::deque<std::vector<char>> messages;
    Wsts::VxWorks::WaitQueue receivers;
    Wsts::VxWorks::WaitQueue senders;  //!< waiting for room
};

struct wdog {
    bool armed = false;
    std::uint64_t deadline = 0;
    std::uint64_t order = 0;
    FUNCPTR routine = nullptr;
    _Vx_usr_arg_t parameter = 0;
};

#endif
