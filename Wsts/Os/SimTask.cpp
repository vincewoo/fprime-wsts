// ======================================================================
// \title  SimTask.cpp
// \brief  The Os::Task of a simulated deployment: a POSIX task that takes its turn
// ======================================================================
#include <Wsts/Os/SimTask.hpp>

#include <atomic>

#include <Drv/Ip/SocketComponentHelper.hpp>
#include <Os/Task.hpp>
#include <Wsts/Core/Activity.hpp>

namespace Wsts {

namespace {

struct Started {
    Os::TaskInterface::taskRoutine routine;
    void* argument;
    std::uint64_t order;  // among the tasks, as they were started
};

std::atomic<std::uint64_t> s_started{0};

// The routines of the framework's socket drivers (Drv.TcpClient, Drv.TcpServer, Drv.Udp). Their
// own threads block in the host (accept, recv) with nothing to do until bytes arrive from
// outside, so they cannot take the turn: a thread that blocks in a system call with the turn
// held keeps every other task, and the tick, from running. What they receive is outside input
// (docs/stepped-time.md): it reaches flight software through queues, which count it. The
// routines are protected members of the helper, reachable from a class derived from it.
struct SocketRoutines : Drv::SocketComponentHelper {
    static bool isSocketRoutine(Os::TaskInterface::taskRoutine routine) {
        return routine == &SocketComponentHelper::readTask || routine == &SocketComponentHelper::reconnectTask;
    }
};

void run(void* pointer) {
    Started* started = static_cast<Started*>(pointer);
    const Started task = *started;
    delete started;
    Activity::get().setTaskThread();
    // While flight code runs one at a time: wait for this task's turn. It is given up when
    // the task first waits for a message, and at the latest when its routine returns.
    (void)Activity::get().enter(Activity::TASK_START_RANK + task.order);
    task.routine(task.argument);
    Activity::get().abandon();
}

}  // namespace

Os::TaskInterface::Status SimTask::start(const Arguments& arguments) {
    // Os::Task hands its delegate a wrapper object, which the POSIX task passes to the new
    // thread and which calls the routine the task was given. That routine is exchanged
    // here for one that takes the turn first. Anything else is started as it is.
    if (arguments.m_routine != Os::Task::TaskRoutineWrapper::run || arguments.m_routine_argument == nullptr) {
        return Os::Posix::Task::PosixTask::start(arguments);
    }
    auto* wrapper = static_cast<Os::Task::TaskRoutineWrapper*>(arguments.m_routine_argument);
    if (SocketRoutines::isSocketRoutine(wrapper->m_user_function)) {
        return Os::Posix::Task::PosixTask::start(arguments);  // outside the turn: see SocketRoutines
    }
    Started* started = new Started{wrapper->m_user_function, wrapper->m_user_argument, s_started.fetch_add(1)};
    wrapper->m_user_function = run;
    wrapper->m_user_argument = started;
    // Known before its thread exists: the tasks begin in the order they were started,
    // however soon the host runs each thread
    const std::uint64_t rank = Activity::TASK_START_RANK + started->order;
    Activity::get().expectTask(rank);
    const Status status = Os::Posix::Task::PosixTask::start(arguments);
    if (status != Status::OP_OK) {
        Activity::get().unexpectTask(rank);
        wrapper->m_user_function = started->routine;
        wrapper->m_user_argument = started->argument;
        delete started;
    }
    return status;
}

Os::TaskInterface::Status SimTask::_delay(const Fw::TimeInterval& interval) {
    Activity& activity = Activity::get();
    // Only where flight code runs one at a time, and only a thread that has the turn: in real
    // time, or for a thread that never took it, this is the POSIX delay as it was
    const bool hadTurn = activity.exclusive() && activity.hasTurn();
    const std::uint64_t rank = hadTurn ? activity.rank() : 0;
    if (hadTurn) {
        activity.abandon();  // however often this thread took it: it takes it back once
    }
    const Status status = Os::Posix::Task::PosixTask::_delay(interval);
    if (hadTurn) {
        (void)activity.enter(rank);
    }
    return status;
}

}  // namespace Wsts
