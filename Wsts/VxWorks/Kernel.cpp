// ======================================================================
// \title  Kernel.cpp
// \brief  The kernel behind the VxWorks API shim: tasks, waiting, the system clock
// ======================================================================
#include <Wsts/VxWorks/Kernel.hpp>

#include <pthread.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>

#include <Os/Task.hpp>
#include <Wsts/Core/Activity.hpp>

namespace Wsts {
namespace VxWorks {

namespace {

thread_local TaskRecord* t_self = nullptr;
thread_local bool t_interrupt = false;

void erase(std::vector<Waiter*>& list, Waiter* waiter) {
    list.erase(std::remove(list.begin(), list.end(), waiter), list.end());
}

// A task of the shim ends here, however it ends (its entry point returns, it deletes itself,
// another task deletes it): what it held goes with it
struct TaskExit {
    TaskRecord* task = nullptr;
    ~TaskExit() {
        if (task == nullptr) {
            return;
        }
        Kernel& kernel = Kernel::get();
        {
            std::lock_guard<std::mutex> lock(kernel.mutex());
            task->finished = true;
            task->waiting = nullptr;
        }
        kernel.notify();
        Activity::get().releaseHeld();
        Activity::get().abandon();
    }
};

}  // namespace

bool interruptLevel() {
    return t_interrupt;
}

void setInterruptLevel(bool on) {
    t_interrupt = on;
}

Kernel& Kernel::get() {
    // Never destroyed: tasks may still call into it while the process exits
    static Kernel* kernel = new Kernel;
    return *kernel;
}

std::uint64_t Kernel::rankOf(const TaskRecord& task) {
    return Activity::TASK_RANK + RANK_BASE + static_cast<std::uint64_t>(task.priority) * 10000u + task.order % 10000u;
}

// ----------------------------------------------------------------------
// Tasks
// ----------------------------------------------------------------------

TaskRecord* Kernel::self() {
    if (t_self != nullptr) {
        return t_self;
    }
    // A thread the shim did not start: flight software's tasks, the main thread, the clock
    TaskRecord* task = new TaskRecord;
    task->id = m_nextId++;
    task->adopted = true;
    task->activated = true;
    task->order = m_created++;
    task->name = "tAdopted" + std::to_string(task->order);
    m_tasks[task->id] = task;
    t_self = task;
    return task;
}

TaskRecord* Kernel::find(TASK_ID id) {
    if (id == TASK_ID_NULL) {
        return this->self();
    }
    const auto found = m_tasks.find(id);
    return found == m_tasks.end() ? nullptr : found->second;
}

TaskRecord* Kernel::byName(const std::string& name) {
    for (const auto& entry : m_tasks) {
        if (!entry.second->finished && entry.second->name == name) {
            return entry.second;
        }
    }
    return nullptr;
}

TASK_ID Kernel::create(const char* name, int priority, int options, FUNCPTR entry, const _Vx_usr_arg_t* args,
                       bool activate) {
    if (priority < 0 || priority > 255) {
        errno = S_taskLib_ILLEGAL_PRIORITY;
        return TASK_ID_ERROR;
    }
    if (entry == nullptr) {
        errno = S_taskLib_ILLEGAL_OPERATION;
        return TASK_ID_ERROR;
    }
    TaskRecord* task = new TaskRecord;
    task->priority = priority;
    task->options = options;
    task->entry = entry;
    std::copy(args, args + 10, task->args);
    task->task = new Os::Task;
    TASK_ID id = TASK_ID_NULL;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        id = task->id = m_nextId++;
        task->order = m_created++;
        task->name = (name != nullptr && name[0] != '\0') ? std::string(name) : "t" + std::to_string(task->order + 1);
        m_tasks[id] = task;
    }
    if (activate && this->activate(id) != OK) {
        return TASK_ID_ERROR;
    }
    return id;
}

STATUS Kernel::activate(TASK_ID id) {
    TaskRecord* task = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        task = this->find(id);
        if (task == nullptr || task->adopted || task->activated || task->finished) {
            errno = S_objLib_OBJ_ID_ERROR;
            return ERROR;
        }
        task->activated = true;
    }
    // The new task is at work from here until it first blocks: a tick that spawns a task
    // waits for what the task does first
    Activity::get().begin();
    const Os::TaskString name(task->name.c_str());
    const Os::Task::Arguments arguments(name, taskMain, task);
    if (task->task->start(arguments) != Os::Task::Status::OP_OK) {
        Activity::get().end();
        std::lock_guard<std::mutex> lock(m_mutex);
        task->finished = true;
        errno = S_taskLib_ILLEGAL_OPERATION;
        return ERROR;
    }
    return OK;
}

void Kernel::taskMain(void* argument) {
    TaskRecord* task = static_cast<TaskRecord*>(argument);
    thread_local TaskExit exit;
    exit.task = task;
    t_self = task;
    Activity::get().setTaskThread();
    Activity::get().hold();  // the piece activate() began for it
    Kernel& kernel = Kernel::get();
    {
        const bool hadTurn = Activity::get().hasTurn();
        std::unique_lock<std::mutex> lock(kernel.m_mutex);
        kernel.checkpoint(lock, hadTurn);  // deleted or suspended before it began
    }
    const _Vx_usr_arg_t* a = task->args;
    (void)task->entry(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9]);
}

void Kernel::exitTask() {
    // Thread-local destructors run on the way out (TaskExit, and what the thread holds)
    pthread_exit(nullptr);
}

void Kernel::suspend(TaskRecord& task) {
    task.suspended = true;
    Waiter* waiter = task.waiting;
    if (waiter != nullptr && waiter->outcome != Waiter::WAITING && waiter->outcome != Waiter::ENDED &&
        waiter->counted) {
        // Woken and not running yet: it is not at work while suspended
        waiter->counted = false;
        Activity::get().end();
    }
}

void Kernel::resume(TaskRecord& task) {
    task.suspended = false;
    Waiter* waiter = task.waiting;
    if (waiter == nullptr) {
        return;
    }
    if (waiter->suspension) {
        this->wake(*waiter, Waiter::GRANTED);
    } else if (waiter->outcome != Waiter::WAITING && !waiter->counted) {
        // Woken while it was suspended: at work from now
        waiter->counted = true;
        Activity::get().begin();
    }
}

void Kernel::requestDelete(TaskRecord& task) {
    task.deleteRequested = true;
    if (!task.activated) {
        task.finished = true;  // never started
    } else if (task.waiting != nullptr) {
        this->wake(*task.waiting, Waiter::ENDED);
    }
}

// ----------------------------------------------------------------------
// Waiting
// ----------------------------------------------------------------------

void Kernel::prepare(Waiter& waiter) {
    if (waiter.task != nullptr) {
        return;
    }
    TaskRecord* task = this->self();
    waiter.task = task;
    // A thread the shim did not start waits with the rank it last ran with
    waiter.rank = task->adopted ? Activity::get().rank() : rankOf(*task);
    waiter.arrival = m_arrivals++;
}

void Kernel::enqueue(WaitQueue& queue, Waiter& waiter, bool byPriority) {
    this->prepare(waiter);
    auto at = queue.end();
    if (byPriority) {
        at = std::find_if(queue.begin(), queue.end(),
                          [&waiter](const Waiter* other) { return other->task->priority > waiter.task->priority; });
    }
    queue.insert(at, &waiter);
    waiter.queue = &queue;
}

void Kernel::wake(Waiter& waiter, Waiter::Outcome outcome) {
    if (waiter.outcome != Waiter::WAITING) {
        return;
    }
    waiter.outcome = outcome;
    if (waiter.queue != nullptr) {
        erase(*waiter.queue, &waiter);
        waiter.queue = nullptr;
    }
    if (waiter.timed) {
        erase(m_timed, &waiter);
    }
    if (outcome == Waiter::ENDED || waiter.suspension || !waiter.task->suspended) {
        // At work from here until it blocks again: the woken thread takes this over
        waiter.counted = true;
        Activity::get().begin();
    }
}

bool Kernel::readyLocked(const Waiter& waiter) const {
    return waiter.outcome != Waiter::WAITING &&
           (waiter.outcome == Waiter::ENDED || waiter.suspension || !waiter.task->suspended);
}

Waiter::Outcome Kernel::block(std::unique_lock<std::mutex>& lock, Waiter& waiter, _Vx_ticks_t timeout, bool hadTurn) {
    this->prepare(waiter);
    TaskRecord* task = waiter.task;
    if (timeout != static_cast<_Vx_ticks_t>(WAIT_FOREVER)) {
        waiter.timed = true;
        waiter.deadline = m_ticks + timeout;
        m_timed.push_back(&waiter);
    }
    task->waiting = &waiter;
    Activity& activity = Activity::get();
    // Blocked: what it was handed is finished
    activity.releaseHeld();
    bool tookTurn = false;
    while (!this->readyLocked(waiter)) {
        if (activity.exclusive()) {
            // Stepped time: wait for the turn, which comes when it is woken and no one of a
            // lower rank is ready
            lock.unlock();
            const Activity::Ready ready = [this, &waiter]() {
                std::lock_guard<std::mutex> guard(m_mutex);
                return this->readyLocked(waiter);
            };
            const bool turn = activity.awaitTurn(waiter.rank, ready);
            lock.lock();
            if (turn) {
                tookTurn = true;
                break;
            }
        } else {
            m_changed.wait_for(lock, std::chrono::milliseconds(20),
                               [this, &waiter, &activity]() { return this->readyLocked(waiter) || activity.exclusive(); });
        }
    }
    if (waiter.timed) {
        erase(m_timed, &waiter);
    }
    if (waiter.queue != nullptr) {
        erase(*waiter.queue, &waiter);
        waiter.queue = nullptr;
    }
    task->waiting = nullptr;
    if (waiter.counted) {
        activity.hold();
    }
    if (tookTurn && !hadTurn) {
        // It did not run in turn before it blocked (start-up code, a thread of the host):
        // it does not keep the turn now
        lock.unlock();
        activity.leave();
        lock.lock();
    }
    return waiter.outcome;
}

void Kernel::checkpoint(std::unique_lock<std::mutex>& lock, bool hadTurn) {
    TaskRecord* task = this->self();
    if (task->adopted) {
        return;
    }
    for (;;) {
        if (task->deleteRequested) {
            lock.unlock();
            this->exitTask();
        }
        if (!task->suspended) {
            return;
        }
        Waiter waiter;
        waiter.suspension = true;
        (void)this->block(lock, waiter, static_cast<_Vx_ticks_t>(WAIT_FOREVER), hadTurn);
    }
}

void Kernel::notify() {
    {
        // Taken so that a waiter cannot miss it between its check and its wait
        std::lock_guard<std::mutex> lock(m_mutex);
    }
    m_changed.notify_all();
    Activity::get().poke();
}

// ----------------------------------------------------------------------
// The system clock
// ----------------------------------------------------------------------

void Kernel::ensureClock() {
    if (m_clockStarted.load()) {
        return;
    }
    std::lock_guard<std::mutex> lock(m_clockMutex);
    if (m_clockStarted.load()) {
        return;
    }
    (void)m_clock.configure(static_cast<double>(m_rate.load()), [this]() { this->onClockTick(); });
    m_clock.start();
    m_clockStarted.store(true);
}

bool Kernel::setClockRate(int ticksPerSecond) {
    if (ticksPerSecond < 1 || static_cast<double>(ticksPerSecond) > CycleClock::MAX_HZ) {
        return false;
    }
    std::lock_guard<std::mutex> lock(m_clockMutex);
    m_rate.store(ticksPerSecond);
    if (m_clockStarted.load()) {
        // Started it stays, for whoever asks meanwhile: its next tick comes at the new rate
        m_clock.stop();
        (void)m_clock.configure(static_cast<double>(ticksPerSecond), [this]() { this->onClockTick(); });
        m_clock.start();
    }
    return true;
}

void Kernel::onClockTick() {
    // A cycle of the simulation's own clock: inside an Activity::Scope, and in stepped time
    // with the turn, at its instant within the simulation tick
    struct Due {
        std::uint64_t deadline;
        std::uint64_t order;
        FUNCPTR routine;
        _Vx_usr_arg_t parameter;
    };
    std::vector<Due> due;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_ticks++;
        const std::vector<Waiter*> timedOut = [this]() {
            std::vector<Waiter*> list;
            for (Waiter* waiter : m_timed) {
                if (waiter->deadline <= m_ticks) {
                    list.push_back(waiter);
                }
            }
            return list;
        }();
        for (Waiter* waiter : timedOut) {
            // A delay that is over is what it waited for
            this->wake(*waiter, waiter->queue == nullptr && !waiter->suspension ? Waiter::GRANTED : Waiter::TIMED_OUT);
        }
        for (wdog* watchdog : watchdogs) {
            if (watchdog->armed && watchdog->deadline <= m_ticks) {
                watchdog->armed = false;
                due.push_back(Due{watchdog->deadline, watchdog->order, watchdog->routine, watchdog->parameter});
            }
        }
    }
    this->notify();
    std::sort(due.begin(), due.end(), [](const Due& a, const Due& b) {
        return a.deadline != b.deadline ? a.deadline < b.deadline : a.order < b.order;
    });
    for (const Due& watchdog : due) {
        std::lock_guard<std::recursive_mutex> level(intLock);
        setInterruptLevel(true);
        (void)watchdog.routine(watchdog.parameter);
        setInterruptLevel(false);
    }
}

}  // namespace VxWorks
}  // namespace Wsts

// ----------------------------------------------------------------------
// taskLib
// ----------------------------------------------------------------------

using Wsts::Activity;
using Wsts::VxWorks::Kernel;
using Wsts::VxWorks::TaskRecord;
using Wsts::VxWorks::Waiter;
using Wsts::VxWorks::interruptLevel;

extern "C" {

TASK_ID taskSpawn(char* name, int priority, int options, size_t stackSize, FUNCPTR entryPt, _Vx_usr_arg_t arg1,
                  _Vx_usr_arg_t arg2, _Vx_usr_arg_t arg3, _Vx_usr_arg_t arg4, _Vx_usr_arg_t arg5, _Vx_usr_arg_t arg6,
                  _Vx_usr_arg_t arg7, _Vx_usr_arg_t arg8, _Vx_usr_arg_t arg9, _Vx_usr_arg_t arg10) {
    (void)stackSize;  // the host's default: a target's stack sizes are too small for it
    const _Vx_usr_arg_t args[10] = {arg1, arg2, arg3, arg4, arg5, arg6, arg7, arg8, arg9, arg10};
    return Kernel::get().create(name, priority, options, entryPt, args, (options & VX_TASK_NOACTIVATE) == 0);
}

TASK_ID taskCreate(char* name, int priority, int options, size_t stackSize, FUNCPTR entryPt, _Vx_usr_arg_t arg1,
                   _Vx_usr_arg_t arg2, _Vx_usr_arg_t arg3, _Vx_usr_arg_t arg4, _Vx_usr_arg_t arg5, _Vx_usr_arg_t arg6,
                   _Vx_usr_arg_t arg7, _Vx_usr_arg_t arg8, _Vx_usr_arg_t arg9, _Vx_usr_arg_t arg10) {
    (void)stackSize;
    const _Vx_usr_arg_t args[10] = {arg1, arg2, arg3, arg4, arg5, arg6, arg7, arg8, arg9, arg10};
    return Kernel::get().create(name, priority, options, entryPt, args, false);
}

STATUS taskActivate(TASK_ID tid) {
    return Kernel::get().activate(tid);
}

STATUS taskDelete(TASK_ID tid) {
    Kernel& kernel = Kernel::get();
    std::unique_lock<std::mutex> lock(kernel.mutex());
    TaskRecord* self = kernel.self();
    TaskRecord* task = kernel.find(tid);
    if (task == nullptr || task->finished) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    if (task->adopted || interruptLevel()) {
        // A thread the shim did not start cannot be ended from outside
        errno = S_taskLib_ILLEGAL_OPERATION;
        return ERROR;
    }
    if (task == self) {
        lock.unlock();
        kernel.exitTask();
    }
    kernel.requestDelete(*task);
    lock.unlock();
    kernel.notify();
    return OK;
}

STATUS taskDeleteForce(TASK_ID tid) {
    return taskDelete(tid);
}

void taskExit(int code) {
    (void)code;
    Kernel::get().exitTask();
}

STATUS taskSuspend(TASK_ID tid) {
    Kernel& kernel = Kernel::get();
    const bool hadTurn = Activity::get().hasTurn();
    std::unique_lock<std::mutex> lock(kernel.mutex());
    TaskRecord* self = kernel.self();
    TaskRecord* task = kernel.find(tid);
    if (task == nullptr || task->finished) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    if (task->adopted) {
        errno = S_taskLib_ILLEGAL_OPERATION;
        return ERROR;
    }
    kernel.suspend(*task);
    if (task == self) {
        kernel.checkpoint(lock, hadTurn);  // until resumed
    }
    return OK;
}

STATUS taskResume(TASK_ID tid) {
    Kernel& kernel = Kernel::get();
    {
        std::lock_guard<std::mutex> lock(kernel.mutex());
        TaskRecord* task = kernel.find(tid);
        if (task == nullptr || task->finished) {
            errno = S_objLib_OBJ_ID_ERROR;
            return ERROR;
        }
        kernel.resume(*task);
    }
    kernel.notify();
    return OK;
}

STATUS taskDelay(_Vx_ticks_t ticks) {
    if (interruptLevel()) {
        errno = S_taskLib_ILLEGAL_OPERATION;
        return ERROR;
    }
    Kernel& kernel = Kernel::get();
    Activity& activity = Activity::get();
    const bool hadTurn = activity.hasTurn();
    if (ticks != 0) {
        kernel.ensureClock();
    }
    std::unique_lock<std::mutex> lock(kernel.mutex());
    kernel.checkpoint(lock, hadTurn);
    if (ticks == 0) {
        // Let others that are ready run first
        TaskRecord* self = kernel.self();
        const std::uint64_t rank = self->adopted ? activity.rank() : Kernel::rankOf(*self);
        lock.unlock();
        if (activity.exclusive() && hadTurn) {
            const Activity::Ready always = []() { return true; };
            (void)activity.awaitTurn(rank, always);
        } else {
            std::this_thread::yield();
        }
        return OK;
    }
    Waiter waiter;
    (void)kernel.block(lock, waiter, ticks, hadTurn);
    kernel.checkpoint(lock, hadTurn);
    return OK;
}

TASK_ID taskIdSelf(void) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    return kernel.self()->id;
}

STATUS taskIdVerify(TASK_ID tid) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    TaskRecord* task = kernel.find(tid);
    if (task == nullptr || task->finished) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    return OK;
}

char* taskName(TASK_ID tid) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    TaskRecord* task = kernel.find(tid);
    // Records are kept for the life of the process: the name stays valid
    return task == nullptr ? nullptr : const_cast<char*>(task->name.c_str());
}

TASK_ID taskNameToId(char* name) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    TaskRecord* task = name == nullptr ? nullptr : kernel.byName(name);
    if (task == nullptr) {
        errno = S_taskLib_NAME_NOT_FOUND;
        return TASK_ID_ERROR;
    }
    return task->id;
}

STATUS taskPrioritySet(TASK_ID tid, int newPriority) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    TaskRecord* task = kernel.find(tid);
    if (task == nullptr || task->finished) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    if (newPriority < 0 || newPriority > 255) {
        errno = S_taskLib_ILLEGAL_PRIORITY;
        return ERROR;
    }
    task->priority = newPriority;
    return OK;
}

STATUS taskPriorityGet(TASK_ID tid, int* pPriority) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    TaskRecord* task = kernel.find(tid);
    if (task == nullptr || task->finished || pPriority == nullptr) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    *pPriority = task->priority;
    return OK;
}

STATUS taskLock(void) {
    return OK;
}

STATUS taskUnlock(void) {
    return OK;
}

STATUS taskSafe(void) {
    return OK;
}

STATUS taskUnsafe(void) {
    return OK;
}

}  // extern "C"
