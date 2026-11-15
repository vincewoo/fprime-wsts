// ======================================================================
// \title  Objects.cpp
// \brief  The VxWorks API shim: semLib, msgQLib, wdLib, sysLib, tickLib, intLib, logLib,
//         errnoLib
// ======================================================================
#include <Wsts/VxWorks/Kernel.hpp>

#include <algorithm>
#include <cerrno>
#include <climits>
#include <cstdio>
#include <cstring>

#include <Wsts/Core/Activity.hpp>

#include <errnoLib.h>
#include <intLib.h>
#include <logLib.h>
#include <sysLib.h>
#include <tickLib.h>

using Wsts::Activity;
using Wsts::VxWorks::Kernel;
using Wsts::VxWorks::TaskRecord;
using Wsts::VxWorks::Waiter;
using Wsts::VxWorks::interruptLevel;

namespace {

bool timed(_Vx_ticks_t timeout) {
    return timeout != static_cast<_Vx_ticks_t>(NO_WAIT) && timeout != static_cast<_Vx_ticks_t>(WAIT_FOREVER);
}

//! What a call that blocked returns
STATUS result(Waiter::Outcome outcome) {
    switch (outcome) {
        case Waiter::GRANTED:
            return OK;
        case Waiter::TIMED_OUT:
            errno = S_objLib_OBJ_TIMEOUT;
            return ERROR;
        default:
            errno = S_objLib_OBJ_DELETED;
            return ERROR;
    }
}

//! Wake every waiter of a queue (the queue empties as they are woken)
void wakeAll(Kernel& kernel, Wsts::VxWorks::WaitQueue& queue, Waiter::Outcome outcome) {
    const Wsts::VxWorks::WaitQueue waiters = queue;
    for (Waiter* waiter : waiters) {
        kernel.wake(*waiter, outcome);
    }
}

void put(msg_q& queue, std::vector<char> message, bool urgent) {
    if (urgent) {
        queue.messages.push_front(std::move(message));
    } else {
        queue.messages.push_back(std::move(message));
    }
}

thread_local int t_intLocks = 0;

}  // namespace

extern "C" {

// ----------------------------------------------------------------------
// semLib
// ----------------------------------------------------------------------

static SEM_ID semCreate(semaphore::Kind kind, int options, int count) {
    semaphore* sem = new semaphore;
    sem->kind = kind;
    sem->options = options;
    sem->count = count;
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    kernel.live.insert(sem);
    return sem;
}

SEM_ID semBCreate(int options, SEM_B_STATE initialState) {
    if (initialState != SEM_EMPTY && initialState != SEM_FULL) {
        errno = S_semLib_INVALID_STATE;
        return SEM_ID_NULL;
    }
    return semCreate(semaphore::BINARY, options, initialState == SEM_FULL ? 1 : 0);
}

SEM_ID semCCreate(int options, int initialCount) {
    if (initialCount < 0) {
        errno = S_semLib_INVALID_STATE;
        return SEM_ID_NULL;
    }
    return semCreate(semaphore::COUNTING, options, initialCount);
}

SEM_ID semMCreate(int options) {
    return semCreate(semaphore::MUTEX, options, 0);
}

STATUS semDelete(SEM_ID semId) {
    Kernel& kernel = Kernel::get();
    {
        std::lock_guard<std::mutex> lock(kernel.mutex());
        if (kernel.live.erase(semId) == 0) {
            errno = S_objLib_OBJ_ID_ERROR;
            return ERROR;
        }
        wakeAll(kernel, semId->waiters, Waiter::DELETED);
        delete semId;
    }
    kernel.notify();
    return OK;
}

STATUS semTake(SEM_ID semId, _Vx_ticks_t timeout) {
    if (interruptLevel()) {
        errno = S_semLib_INVALID_OPERATION;  // not at interrupt level
        return ERROR;
    }
    Kernel& kernel = Kernel::get();
    const bool hadTurn = Activity::get().hasTurn();
    if (timed(timeout)) {
        kernel.ensureClock();
    }
    std::unique_lock<std::mutex> lock(kernel.mutex());
    kernel.checkpoint(lock, hadTurn);
    if (kernel.live.count(semId) == 0) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    TaskRecord* self = kernel.self();
    if (semId->kind == semaphore::MUTEX) {
        if (semId->owner == self) {
            semId->depth++;
            return OK;
        }
        if (semId->owner == nullptr) {
            semId->owner = self;
            semId->depth = 1;
            return OK;
        }
    } else if (semId->count > 0) {
        semId->count--;
        return OK;
    }
    if (timeout == static_cast<_Vx_ticks_t>(NO_WAIT)) {
        errno = S_objLib_OBJ_UNAVAILABLE;
        return ERROR;
    }
    Waiter waiter;
    kernel.enqueue(semId->waiters, waiter, (semId->options & SEM_Q_PRIORITY) != 0);
    const Waiter::Outcome outcome = kernel.block(lock, waiter, timeout, hadTurn);
    kernel.checkpoint(lock, hadTurn);
    return result(outcome);
}

STATUS semGive(SEM_ID semId) {
    Kernel& kernel = Kernel::get();
    {
        std::lock_guard<std::mutex> lock(kernel.mutex());
        if (kernel.live.count(semId) == 0) {
            errno = S_objLib_OBJ_ID_ERROR;
            return ERROR;
        }
        if (semId->kind == semaphore::MUTEX) {
            if (interruptLevel() || semId->owner != kernel.self()) {
                errno = S_semLib_INVALID_OPERATION;  // only its owner gives a mutex
                return ERROR;
            }
            if (--semId->depth > 0) {
                return OK;
            }
            semId->owner = nullptr;
            if (!semId->waiters.empty()) {
                // Straight to the first waiter, as the kernel does
                Waiter* next = semId->waiters.front();
                semId->owner = next->task;
                semId->depth = 1;
                kernel.wake(*next, Waiter::GRANTED);
            }
        } else if (!semId->waiters.empty()) {
            kernel.wake(*semId->waiters.front(), Waiter::GRANTED);
        } else if (semId->kind == semaphore::BINARY) {
            semId->count = 1;
        } else if (semId->count < INT_MAX) {
            semId->count++;
        }
    }
    kernel.notify();
    return OK;
}

STATUS semFlush(SEM_ID semId) {
    Kernel& kernel = Kernel::get();
    {
        std::lock_guard<std::mutex> lock(kernel.mutex());
        if (kernel.live.count(semId) == 0) {
            errno = S_objLib_OBJ_ID_ERROR;
            return ERROR;
        }
        if (semId->kind == semaphore::MUTEX) {
            errno = S_semLib_INVALID_OPERATION;
            return ERROR;
        }
        wakeAll(kernel, semId->waiters, Waiter::GRANTED);
    }
    kernel.notify();
    return OK;
}

// ----------------------------------------------------------------------
// msgQLib
// ----------------------------------------------------------------------

MSG_Q_ID msgQCreate(size_t maxMsgs, size_t maxMsgLength, int options) {
    msg_q* queue = new msg_q;
    queue->maxMsgs = maxMsgs;
    queue->maxLength = maxMsgLength;
    queue->options = options;
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    kernel.live.insert(queue);
    return queue;
}

STATUS msgQDelete(MSG_Q_ID msgQId) {
    Kernel& kernel = Kernel::get();
    {
        std::lock_guard<std::mutex> lock(kernel.mutex());
        if (kernel.live.erase(msgQId) == 0) {
            errno = S_objLib_OBJ_ID_ERROR;
            return ERROR;
        }
        wakeAll(kernel, msgQId->receivers, Waiter::DELETED);
        wakeAll(kernel, msgQId->senders, Waiter::DELETED);
        delete msgQId;
    }
    kernel.notify();
    return OK;
}

STATUS msgQSend(MSG_Q_ID msgQId, char* buffer, size_t nBytes, _Vx_ticks_t timeout, int priority) {
    if (interruptLevel() && timeout != static_cast<_Vx_ticks_t>(NO_WAIT)) {
        errno = S_msgQLib_NON_ZERO_TIMEOUT_AT_INT_LEVEL;
        return ERROR;
    }
    Kernel& kernel = Kernel::get();
    const bool hadTurn = Activity::get().hasTurn();
    if (timed(timeout)) {
        kernel.ensureClock();
    }
    std::unique_lock<std::mutex> lock(kernel.mutex());
    if (!interruptLevel()) {
        kernel.checkpoint(lock, hadTurn);
    }
    if (kernel.live.count(msgQId) == 0) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    if (nBytes > msgQId->maxLength || (buffer == nullptr && nBytes > 0)) {
        errno = S_msgQLib_INVALID_MSG_LENGTH;
        return ERROR;
    }
    std::vector<char> message(buffer, buffer + nBytes);
    const bool urgent = priority == MSG_PRI_URGENT;
    if (!msgQId->receivers.empty()) {
        // Straight to the first receiver, as the kernel does
        Waiter* receiver = msgQId->receivers.front();
        receiver->message = std::move(message);
        kernel.wake(*receiver, Waiter::GRANTED);
        lock.unlock();
        kernel.notify();
        return OK;
    }
    if (msgQId->messages.size() < msgQId->maxMsgs) {
        put(*msgQId, std::move(message), urgent);
        return OK;
    }
    if (timeout == static_cast<_Vx_ticks_t>(NO_WAIT)) {
        errno = S_objLib_OBJ_UNAVAILABLE;
        return ERROR;
    }
    // Full: wait for room. The receiver that makes it puts this message in.
    Waiter waiter;
    waiter.message = std::move(message);
    waiter.urgent = urgent;
    kernel.enqueue(msgQId->senders, waiter, (msgQId->options & MSG_Q_PRIORITY) != 0);
    const Waiter::Outcome outcome = kernel.block(lock, waiter, timeout, hadTurn);
    kernel.checkpoint(lock, hadTurn);
    return result(outcome);
}

ssize_t msgQReceive(MSG_Q_ID msgQId, char* buffer, size_t maxNBytes, _Vx_ticks_t timeout) {
    if (interruptLevel() && timeout != static_cast<_Vx_ticks_t>(NO_WAIT)) {
        errno = S_msgQLib_NON_ZERO_TIMEOUT_AT_INT_LEVEL;
        return ERROR;
    }
    Kernel& kernel = Kernel::get();
    const bool hadTurn = Activity::get().hasTurn();
    if (timed(timeout)) {
        kernel.ensureClock();
    }
    std::unique_lock<std::mutex> lock(kernel.mutex());
    if (!interruptLevel()) {
        kernel.checkpoint(lock, hadTurn);
    }
    if (kernel.live.count(msgQId) == 0) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    std::vector<char> message;
    if (!msgQId->messages.empty()) {
        message = std::move(msgQId->messages.front());
        msgQId->messages.pop_front();
        bool woke = false;
        if (!msgQId->senders.empty()) {
            // Room for the first sender that waits for it
            Waiter* sender = msgQId->senders.front();
            put(*msgQId, std::move(sender->message), sender->urgent);
            kernel.wake(*sender, Waiter::GRANTED);
            woke = true;
        }
        lock.unlock();
        if (woke) {
            kernel.notify();
        }
    } else if (!msgQId->senders.empty()) {
        // A queue of no room at all: take the first sender's message from it directly
        Waiter* sender = msgQId->senders.front();
        message = std::move(sender->message);
        kernel.wake(*sender, Waiter::GRANTED);
        lock.unlock();
        kernel.notify();
    } else {
        if (timeout == static_cast<_Vx_ticks_t>(NO_WAIT)) {
            errno = S_objLib_OBJ_UNAVAILABLE;
            return ERROR;
        }
        Waiter waiter;
        kernel.enqueue(msgQId->receivers, waiter, (msgQId->options & MSG_Q_PRIORITY) != 0);
        const Waiter::Outcome outcome = kernel.block(lock, waiter, timeout, hadTurn);
        kernel.checkpoint(lock, hadTurn);
        if (result(outcome) != OK) {
            return ERROR;
        }
        message = std::move(waiter.message);
    }
    const std::size_t size = std::min(message.size(), maxNBytes);
    if (size > 0 && buffer != nullptr) {
        std::memcpy(buffer, message.data(), size);
    }
    return static_cast<ssize_t>(size);
}

int msgQNumMsgs(MSG_Q_ID msgQId) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    if (kernel.live.count(msgQId) == 0) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    return static_cast<int>(msgQId->messages.size());
}

// ----------------------------------------------------------------------
// wdLib
// ----------------------------------------------------------------------

WDOG_ID wdCreate(void) {
    wdog* watchdog = new wdog;
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    kernel.live.insert(watchdog);
    kernel.watchdogs.push_back(watchdog);
    return watchdog;
}

STATUS wdDelete(WDOG_ID wdId) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    if (kernel.live.erase(wdId) == 0) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    kernel.watchdogs.erase(std::remove(kernel.watchdogs.begin(), kernel.watchdogs.end(), wdId), kernel.watchdogs.end());
    delete wdId;
    return OK;
}

STATUS wdStart(WDOG_ID wdId, _Vx_ticks_t delay, FUNCPTR pRoutine, _Vx_usr_arg_t parameter) {
    Kernel& kernel = Kernel::get();
    kernel.ensureClock();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    if (kernel.live.count(wdId) == 0 || pRoutine == nullptr) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    wdId->armed = true;
    wdId->deadline = kernel.ticks() + std::max<_Vx_ticks_t>(delay, 1);
    wdId->order = kernel.armed++;
    wdId->routine = pRoutine;
    wdId->parameter = parameter;
    return OK;
}

STATUS wdCancel(WDOG_ID wdId) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    if (kernel.live.count(wdId) == 0) {
        errno = S_objLib_OBJ_ID_ERROR;
        return ERROR;
    }
    wdId->armed = false;
    return OK;
}

// ----------------------------------------------------------------------
// sysLib, tickLib
// ----------------------------------------------------------------------

int sysClkRateGet(void) {
    return Kernel::get().clockRate();
}

STATUS sysClkRateSet(int ticksPerSecond) {
    return Kernel::get().setClockRate(ticksPerSecond) ? OK : ERROR;
}

_Vx_ticks64_t tick64Get(void) {
    Kernel& kernel = Kernel::get();
    kernel.ensureClock();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    return static_cast<_Vx_ticks64_t>(kernel.ticks());
}

_Vx_ticks_t tickGet(void) {
    return static_cast<_Vx_ticks_t>(tick64Get());
}

// ----------------------------------------------------------------------
// intLib
// ----------------------------------------------------------------------

BOOL intContext(void) {
    return interruptLevel() ? TRUE : FALSE;
}

int intLock(void) {
    Kernel::get().intLock.lock();
    return ++t_intLocks;
}

void intUnlock(int lockKey) {
    (void)lockKey;
    if (t_intLocks > 0) {
        t_intLocks--;
        Kernel::get().intLock.unlock();
    }
}

// ----------------------------------------------------------------------
// logLib, errnoLib
// ----------------------------------------------------------------------

int logMsg(char* fmt, _Vx_usr_arg_t arg1, _Vx_usr_arg_t arg2, _Vx_usr_arg_t arg3, _Vx_usr_arg_t arg4,
           _Vx_usr_arg_t arg5, _Vx_usr_arg_t arg6) {
    if (fmt == nullptr) {
        return ERROR;
    }
    char prefix[64];
    if (interruptLevel()) {
        std::snprintf(prefix, sizeof prefix, "interrupt: ");
    } else {
        Kernel& kernel = Kernel::get();
        std::lock_guard<std::mutex> lock(kernel.mutex());
        const TaskRecord* self = kernel.self();
        std::snprintf(prefix, sizeof prefix, "0x%lx (%s): ", static_cast<unsigned long>(self->id), self->name.c_str());
    }
    char text[512];
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wformat-nonliteral"
#pragma GCC diagnostic ignored "-Wformat-security"
    std::snprintf(text, sizeof text, fmt, arg1, arg2, arg3, arg4, arg5, arg6);
#pragma GCC diagnostic pop
    const int written = std::fprintf(stderr, "%s%s", prefix, text);
    std::fflush(stderr);
    return written;
}

int errnoGet(void) {
    return errno;
}

STATUS errnoSet(int errorValue) {
    errno = errorValue;
    return OK;
}

}  // extern "C"
