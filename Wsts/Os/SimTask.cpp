// ======================================================================
// \title  SimTask.cpp
// \brief  The Os::Task of a simulated deployment: a POSIX task that takes its turn
// ======================================================================
#include <Wsts/Os/SimTask.hpp>

#include <atomic>

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

void run(void* pointer) {
    Started* started = static_cast<Started*>(pointer);
    const Started task = *started;
    delete started;
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
    Started* started = new Started{wrapper->m_user_function, wrapper->m_user_argument, s_started.fetch_add(1)};
    wrapper->m_user_function = run;
    wrapper->m_user_argument = started;
    const Status status = Os::Posix::Task::PosixTask::start(arguments);
    if (status != Status::OP_OK) {
        wrapper->m_user_function = started->routine;
        wrapper->m_user_argument = started->argument;
        delete started;
    }
    return status;
}

}  // namespace Wsts
