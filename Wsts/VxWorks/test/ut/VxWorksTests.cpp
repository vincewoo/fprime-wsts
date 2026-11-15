// ======================================================================
// \title  VxWorksTests.cpp
// \brief  The VxWorks API shim: what the calls do, in real time and in stepped time
//
// The tasks are those of a simulated deployment (Wsts_Os_Task). The system clock follows
// the simulation's ticks, which the built-in ticker gives here. Control is set up once and
// not reset between tests: the shim's clock stays registered with it.
// ======================================================================
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <cstring>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <Wsts/Core/Activity.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/VxWorks/Kernel.hpp>

#include <errnoLib.h>
#include <intLib.h>
#include <logLib.h>
#include <msgQLib.h>
#include <semLib.h>
#include <sysLib.h>
#include <taskLib.h>
#include <tickLib.h>
#include <vxWorks.h>
#include <wdLib.h>

using Wsts::Control;
using Wsts::VxWorks::Kernel;

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}

namespace {

bool eventually(const std::function<bool()>& condition, std::chrono::milliseconds limit = std::chrono::seconds(5)) {
    const auto until = std::chrono::steady_clock::now() + limit;
    while (!condition()) {
        if (std::chrono::steady_clock::now() > until) {
            return false;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return true;
}

//! Whether a task is blocked in the shim
bool blocked(TASK_ID tid) {
    Kernel& kernel = Kernel::get();
    std::lock_guard<std::mutex> lock(kernel.mutex());
    const Wsts::VxWorks::TaskRecord* task = kernel.find(tid);
    return task != nullptr && task->waiting != nullptr;
}

bool finished(TASK_ID tid) {
    return taskIdVerify(tid) == ERROR;
}

//! A task that runs a function: the entry point gets it as its first argument
int run(_Vx_usr_arg_t argument) {
    (*reinterpret_cast<std::function<void()>*>(argument))();
    return 0;
}

TASK_ID spawn(const char* name, int priority, std::function<void()>* body) {
    return taskSpawn(const_cast<char*>(name), priority, 0, 16384, reinterpret_cast<FUNCPTR>(run),
                     reinterpret_cast<_Vx_usr_arg_t>(body), 0, 0, 0, 0, 0, 0, 0, 0, 0);
}

std::mutex s_logMutex;
std::vector<std::string> s_log;

void note(const std::string& text) {
    std::lock_guard<std::mutex> lock(s_logMutex);
    s_log.push_back(text);
}

std::vector<std::string> taken() {
    std::lock_guard<std::mutex> lock(s_logMutex);
    std::vector<std::string> log;
    log.swap(s_log);
    return log;
}

//! The simulation runs in real time, ticking at 60 Hz (the system clock's default rate)
class RealTime : public ::testing::Test {
  protected:
    void SetUp() override {
        Control& control = Control::get();
        control.setStepped(false);
        std::string error;
        (void)control.execLine(R"({"op": "resume"})", error);
        control.startInternalTicker(60);
        taken();
    }
    void TearDown() override { Control::get().stopInternalTicker(); }
};

}  // namespace

// ----------------------------------------------------------------------
// Semaphores
// ----------------------------------------------------------------------

TEST_F(RealTime, AGiveGoesToTheWaiterOfHighestPriority) {
    SEM_ID sem = semBCreate(SEM_Q_PRIORITY, SEM_EMPTY);
    ASSERT_NE(sem, SEM_ID_NULL);
    std::function<void()> low = [sem]() {
        ASSERT_EQ(semTake(sem, WAIT_FOREVER), OK);
        note("low");
    };
    std::function<void()> high = [sem]() {
        ASSERT_EQ(semTake(sem, WAIT_FOREVER), OK);
        note("high");
    };
    const TASK_ID first = spawn("tLow", 200, &low);
    ASSERT_TRUE(eventually([first]() { return blocked(first); }));
    const TASK_ID second = spawn("tHigh", 50, &high);
    ASSERT_TRUE(eventually([second]() { return blocked(second); }));
    ASSERT_EQ(semGive(sem), OK);
    ASSERT_TRUE(eventually([second]() { return finished(second); }));
    EXPECT_TRUE(blocked(first)) << "one give, one task";
    ASSERT_EQ(semGive(sem), OK);
    ASSERT_TRUE(eventually([first]() { return finished(first); }));
    EXPECT_EQ(taken(), (std::vector<std::string>{"high", "low"}));
    EXPECT_EQ(semDelete(sem), OK);
}

TEST_F(RealTime, FifoSemaphoresServeInTheOrderTasksPended) {
    SEM_ID sem = semCCreate(SEM_Q_FIFO, 0);
    std::function<void()> low = [sem]() {
        ASSERT_EQ(semTake(sem, WAIT_FOREVER), OK);
        note("first");
    };
    std::function<void()> high = [sem]() {
        ASSERT_EQ(semTake(sem, WAIT_FOREVER), OK);
        note("second");
    };
    const TASK_ID first = spawn("tFirst", 200, &low);
    ASSERT_TRUE(eventually([first]() { return blocked(first); }));
    const TASK_ID second = spawn("tSecond", 50, &high);
    ASSERT_TRUE(eventually([second]() { return blocked(second); }));
    ASSERT_EQ(semGive(sem), OK);
    ASSERT_TRUE(eventually([first]() { return finished(first); }));
    ASSERT_EQ(semGive(sem), OK);
    ASSERT_TRUE(eventually([second]() { return finished(second); }));
    EXPECT_EQ(taken(), (std::vector<std::string>{"first", "second"}));
    // A counting semaphore counts gives that nobody waits for
    ASSERT_EQ(semGive(sem), OK);
    ASSERT_EQ(semGive(sem), OK);
    EXPECT_EQ(semTake(sem, NO_WAIT), OK);
    EXPECT_EQ(semTake(sem, NO_WAIT), OK);
    EXPECT_EQ(semTake(sem, NO_WAIT), ERROR);
    EXPECT_EQ(errnoGet(), S_objLib_OBJ_UNAVAILABLE);
    semDelete(sem);
}

TEST_F(RealTime, MutexesAreRecursiveAndOwned) {
    SEM_ID mutex = semMCreate(SEM_Q_PRIORITY | SEM_INVERSION_SAFE | SEM_DELETE_SAFE);
    ASSERT_EQ(semTake(mutex, WAIT_FOREVER), OK);
    ASSERT_EQ(semTake(mutex, WAIT_FOREVER), OK) << "recursive";
    std::atomic<STATUS> foreign{OK};
    std::atomic<bool> got{false};
    std::function<void()> other = [&]() {
        foreign = semGive(mutex);  // not its owner
        ASSERT_EQ(semTake(mutex, WAIT_FOREVER), OK);
        got = true;
        semGive(mutex);
    };
    const TASK_ID tid = spawn("tOther", 100, &other);
    ASSERT_TRUE(eventually([tid]() { return blocked(tid); }));
    EXPECT_EQ(foreign.load(), ERROR);
    ASSERT_EQ(semGive(mutex), OK);
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    EXPECT_FALSE(got.load()) << "taken twice, given once: still the main thread's";
    ASSERT_EQ(semGive(mutex), OK);
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_TRUE(got.load());
    EXPECT_EQ(semFlush(mutex), ERROR);
    semDelete(mutex);
}

TEST_F(RealTime, ATimeoutIsCountedInSystemClockTicks) {
    SEM_ID sem = semBCreate(SEM_Q_FIFO, SEM_EMPTY);
    const _Vx_ticks_t before = tickGet();
    EXPECT_EQ(semTake(sem, 6), ERROR);
    EXPECT_EQ(errnoGet(), S_objLib_OBJ_TIMEOUT);
    EXPECT_GE(tickGet() - before, 6u);
    EXPECT_LE(tickGet() - before, 9u);
    semDelete(sem);
}

TEST_F(RealTime, DeletingASemaphoreReleasesItsWaiters) {
    SEM_ID sem = semBCreate(SEM_Q_FIFO, SEM_EMPTY);
    std::atomic<int> error{0};
    std::function<void()> body = [&]() {
        EXPECT_EQ(semTake(sem, WAIT_FOREVER), ERROR);
        error = errnoGet();
    };
    const TASK_ID tid = spawn("tPended", 100, &body);
    ASSERT_TRUE(eventually([tid]() { return blocked(tid); }));
    ASSERT_EQ(semDelete(sem), OK);
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_EQ(error.load(), S_objLib_OBJ_DELETED);
    EXPECT_EQ(semGive(sem), ERROR) << "a deleted ID";
    EXPECT_EQ(errnoGet(), S_objLib_OBJ_ID_ERROR);
}

TEST_F(RealTime, FlushReleasesEveryWaiter) {
    SEM_ID sem = semBCreate(SEM_Q_FIFO, SEM_EMPTY);
    std::function<void()> body = [sem]() { EXPECT_EQ(semTake(sem, WAIT_FOREVER), OK); };
    const TASK_ID a = spawn("tA", 100, &body);
    const TASK_ID b = spawn("tB", 100, &body);
    ASSERT_TRUE(eventually([a, b]() { return blocked(a) && blocked(b); }));
    ASSERT_EQ(semFlush(sem), OK);
    ASSERT_TRUE(eventually([a, b]() { return finished(a) && finished(b); }));
    EXPECT_EQ(semTake(sem, NO_WAIT), ERROR) << "a flush leaves it empty";
    semDelete(sem);
}

// ----------------------------------------------------------------------
// Message queues
// ----------------------------------------------------------------------

TEST_F(RealTime, MessagesKeepTheirOrderAndUrgentOnesGoFirst) {
    MSG_Q_ID queue = msgQCreate(4, 8, MSG_Q_FIFO);
    ASSERT_NE(queue, MSG_Q_ID_NULL);
    ASSERT_EQ(msgQSend(queue, const_cast<char*>("one"), 4, NO_WAIT, MSG_PRI_NORMAL), OK);
    ASSERT_EQ(msgQSend(queue, const_cast<char*>("two"), 4, NO_WAIT, MSG_PRI_NORMAL), OK);
    ASSERT_EQ(msgQSend(queue, const_cast<char*>("now"), 4, NO_WAIT, MSG_PRI_URGENT), OK);
    EXPECT_EQ(msgQNumMsgs(queue), 3);
    EXPECT_EQ(msgQSend(queue, const_cast<char*>("too long!"), 10, NO_WAIT, MSG_PRI_NORMAL), ERROR);
    EXPECT_EQ(errnoGet(), S_msgQLib_INVALID_MSG_LENGTH);
    char buffer[8] = {};
    EXPECT_EQ(msgQReceive(queue, buffer, sizeof buffer, NO_WAIT), 4);
    EXPECT_STREQ(buffer, "now");
    EXPECT_EQ(msgQReceive(queue, buffer, 2, NO_WAIT), 2) << "cut to the buffer";
    EXPECT_EQ(std::string(buffer, 2), "on");
    EXPECT_EQ(msgQReceive(queue, buffer, sizeof buffer, NO_WAIT), 4);
    EXPECT_STREQ(buffer, "two");
    EXPECT_EQ(msgQReceive(queue, buffer, sizeof buffer, NO_WAIT), ERROR);
    EXPECT_EQ(errnoGet(), S_objLib_OBJ_UNAVAILABLE);
    msgQDelete(queue);
}

TEST_F(RealTime, ASenderWaitsForRoomAndAReceiverForAMessage) {
    MSG_Q_ID queue = msgQCreate(1, 4, MSG_Q_FIFO);
    ASSERT_EQ(msgQSend(queue, const_cast<char*>("a"), 2, NO_WAIT, MSG_PRI_NORMAL), OK);
    EXPECT_EQ(msgQSend(queue, const_cast<char*>("b"), 2, NO_WAIT, MSG_PRI_NORMAL), ERROR) << "full";
    std::function<void()> sender = [queue]() {
        EXPECT_EQ(msgQSend(queue, const_cast<char*>("b"), 2, WAIT_FOREVER, MSG_PRI_NORMAL), OK);
    };
    const TASK_ID tid = spawn("tSender", 100, &sender);
    ASSERT_TRUE(eventually([tid]() { return blocked(tid); }));
    char buffer[4] = {};
    EXPECT_EQ(msgQReceive(queue, buffer, sizeof buffer, NO_WAIT), 2);
    EXPECT_STREQ(buffer, "a");
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_EQ(msgQReceive(queue, buffer, sizeof buffer, NO_WAIT), 2);
    EXPECT_STREQ(buffer, "b");

    std::string received;
    std::function<void()> receiver = [&]() {
        char text[4] = {};
        EXPECT_EQ(msgQReceive(queue, text, sizeof text, WAIT_FOREVER), 2);
        received = text;
    };
    const TASK_ID other = spawn("tReceiver", 100, &receiver);
    ASSERT_TRUE(eventually([other]() { return blocked(other); }));
    ASSERT_EQ(msgQSend(queue, const_cast<char*>("c"), 2, NO_WAIT, MSG_PRI_NORMAL), OK);
    ASSERT_TRUE(eventually([other]() { return finished(other); }));
    EXPECT_EQ(received, "c");
    EXPECT_EQ(msgQNumMsgs(queue), 0) << "handed to the receiver, not queued";
    msgQDelete(queue);
}

// ----------------------------------------------------------------------
// Time: delays, watchdogs, the system clock
// ----------------------------------------------------------------------

TEST_F(RealTime, ADelayIsCountedInSystemClockTicks) {
    EXPECT_EQ(sysClkRateGet(), 60);
    std::atomic<_Vx_ticks_t> waited{0};
    std::function<void()> body = [&]() {
        const _Vx_ticks_t before = tickGet();
        EXPECT_EQ(taskDelay(5), OK);
        waited = tickGet() - before;
    };
    const TASK_ID tid = spawn("tDelay", 100, &body);
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_GE(waited.load(), 5u);
    EXPECT_LE(waited.load(), 6u);
}

namespace {
std::atomic<int> s_fired{0};
std::atomic<bool> s_atInterruptLevel{false};
std::atomic<_Vx_ticks_t> s_firedAt{0};
WDOG_ID s_watchdog = WDOG_ID_NULL;
SEM_ID s_fromInterrupt = SEM_ID_NULL;

void watchdogRoutine(_Vx_usr_arg_t again) {
    s_atInterruptLevel = intContext() == TRUE;
    s_firedAt = tickGet();
    if (++s_fired < static_cast<int>(again)) {
        wdStart(s_watchdog, 2, reinterpret_cast<FUNCPTR>(watchdogRoutine), again);
    }
    semGive(s_fromInterrupt);
}
}  // namespace

TEST_F(RealTime, AWatchdogRunsAtInterruptLevelWhenItIsDue) {
    s_watchdog = wdCreate();
    s_fromInterrupt = semCCreate(SEM_Q_FIFO, 0);
    s_fired = 0;
    const _Vx_ticks_t start = tickGet();
    ASSERT_EQ(wdStart(s_watchdog, 3, reinterpret_cast<FUNCPTR>(watchdogRoutine), 4), OK);
    for (int i = 0; i < 4; i++) {
        ASSERT_EQ(semTake(s_fromInterrupt, 60), OK);
    }
    EXPECT_EQ(s_fired.load(), 4);
    EXPECT_TRUE(s_atInterruptLevel.load());
    EXPECT_EQ(s_firedAt.load() - start, 3u + 2u * 3u) << "due at 3, then every 2 ticks";
    EXPECT_FALSE(intContext());
    // Cancelled before it is due: it does not run
    ASSERT_EQ(wdStart(s_watchdog, 2, reinterpret_cast<FUNCPTR>(watchdogRoutine), 99), OK);
    ASSERT_EQ(wdCancel(s_watchdog), OK);
    EXPECT_EQ(semTake(s_fromInterrupt, 6), ERROR);
    EXPECT_EQ(s_fired.load(), 4);
    EXPECT_EQ(wdDelete(s_watchdog), OK);
    EXPECT_EQ(wdStart(s_watchdog, 1, reinterpret_cast<FUNCPTR>(watchdogRoutine), 1), ERROR);
    semDelete(s_fromInterrupt);
}

namespace {
std::atomic<int> s_rearmed{0};
WDOG_ID s_rearming = WDOG_ID_NULL;
void rearm(_Vx_usr_arg_t) {
    s_rearmed++;
    wdStart(s_rearming, 1, reinterpret_cast<FUNCPTR>(rearm), 0);
}
}  // namespace

TEST_F(RealTime, TheClockRateChangesWhileAWatchdogRunsOnEveryTick) {
    s_rearming = wdCreate();
    s_rearmed = 0;
    ASSERT_EQ(wdStart(s_rearming, 1, reinterpret_cast<FUNCPTR>(rearm), 0), OK);
    ASSERT_TRUE(eventually([]() { return s_rearmed > 3; }));
    for (int i = 0; i < 20; i++) {
        ASSERT_EQ(sysClkRateSet(i % 2 == 0 ? 120 : 60), OK) << "while a routine restarts its watchdog";
    }
    EXPECT_EQ(sysClkRateGet(), 60);
    EXPECT_EQ(sysClkRateSet(0), ERROR);
    const int before = s_rearmed.load();
    EXPECT_TRUE(eventually([before]() { return s_rearmed > before + 3; })) << "and it goes on";
    wdCancel(s_rearming);
    wdDelete(s_rearming);
}

TEST_F(RealTime, TheSystemClockStopsWhileTheSimulationIsPaused) {
    (void)tickGet();
    std::string error;
    ASSERT_TRUE(Control::get().execLine(R"({"op": "pause"})", error));
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    const _Vx_ticks_t paused = tickGet();
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    EXPECT_LE(tickGet() - paused, 1u);
    ASSERT_TRUE(Control::get().execLine(R"({"op": "resume"})", error));
    ASSERT_TRUE(eventually([paused]() { return tickGet() - paused >= 5; }));
}

// ----------------------------------------------------------------------
// Tasks
// ----------------------------------------------------------------------

TEST_F(RealTime, TasksHaveNamesPrioritiesAndIds) {
    std::atomic<TASK_ID> self{TASK_ID_NULL};
    SEM_ID go = semBCreate(SEM_Q_FIFO, SEM_EMPTY);
    std::function<void()> body = [&]() {
        self = taskIdSelf();
        semTake(go, WAIT_FOREVER);
    };
    const TASK_ID tid = spawn("tNamed", 120, &body);
    ASSERT_NE(tid, TASK_ID_ERROR);
    ASSERT_TRUE(eventually([tid]() { return blocked(tid); }));
    EXPECT_EQ(self.load(), tid);
    EXPECT_STREQ(taskName(tid), "tNamed");
    EXPECT_EQ(taskNameToId(const_cast<char*>("tNamed")), tid);
    int priority = 0;
    ASSERT_EQ(taskPriorityGet(tid, &priority), OK);
    EXPECT_EQ(priority, 120);
    EXPECT_EQ(taskPrioritySet(tid, 300), ERROR);
    EXPECT_EQ(errnoGet(), S_taskLib_ILLEGAL_PRIORITY);
    ASSERT_EQ(taskPrioritySet(tid, 10), OK);
    semGive(go);
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_EQ(taskNameToId(const_cast<char*>("tNamed")), TASK_ID_ERROR);
    EXPECT_EQ(errnoGet(), S_taskLib_NAME_NOT_FOUND);
    EXPECT_NE(taskIdSelf(), TASK_ID_NULL) << "a thread the shim did not start has an ID too";
    EXPECT_EQ(taskDelete(taskIdSelf()), ERROR) << "and cannot be deleted";
    semDelete(go);
}

TEST_F(RealTime, ATaskCreatedIsStartedByActivate) {
    std::atomic<bool> ran{false};
    std::function<void()> body = [&]() { ran = true; };
    const TASK_ID tid = taskCreate(const_cast<char*>("tLater"), 100, 0, 4096, reinterpret_cast<FUNCPTR>(run),
                                   reinterpret_cast<_Vx_usr_arg_t>(&body), 0, 0, 0, 0, 0, 0, 0, 0, 0);
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    EXPECT_FALSE(ran.load());
    ASSERT_EQ(taskActivate(tid), OK);
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_TRUE(ran.load());
    EXPECT_EQ(taskActivate(tid), ERROR);
}

TEST_F(RealTime, ADeletedTaskEndsAtItsNextCallIntoTheShim) {
    SEM_ID sem = semBCreate(SEM_Q_FIFO, SEM_EMPTY);
    std::atomic<bool> after{false};
    std::function<void()> body = [&]() {
        semTake(sem, WAIT_FOREVER);
        after = true;
    };
    const TASK_ID tid = spawn("tDoomed", 100, &body);
    ASSERT_TRUE(eventually([tid]() { return blocked(tid); }));
    ASSERT_EQ(taskDelete(tid), OK);
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
    EXPECT_FALSE(after.load()) << "it ended in semTake";
    ASSERT_EQ(semGive(sem), OK);
    EXPECT_EQ(semTake(sem, NO_WAIT), OK) << "the deleted task no longer waited for it";
    // A task that deletes itself
    std::function<void()> self = [&]() {
        taskDelete(0);
        after = true;
    };
    const TASK_ID other = spawn("tSelf", 100, &self);
    ASSERT_TRUE(eventually([other]() { return finished(other); }));
    EXPECT_FALSE(after.load());
    semDelete(sem);
}

TEST_F(RealTime, ASuspendedTaskDoesNotRunUntilResumed) {
    SEM_ID sem = semCCreate(SEM_Q_FIFO, 0);
    std::atomic<int> count{0};
    std::function<void()> body = [&]() {
        while (semTake(sem, WAIT_FOREVER) == OK) {
            count++;
        }
    };
    const TASK_ID tid = spawn("tCounter", 100, &body);
    ASSERT_TRUE(eventually([tid]() { return blocked(tid); }));
    semGive(sem);
    ASSERT_TRUE(eventually([&count]() { return count == 1; }));
    ASSERT_EQ(taskSuspend(tid), OK);
    semGive(sem);
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    EXPECT_EQ(count.load(), 1) << "it was given the semaphore, and is suspended";
    ASSERT_EQ(taskResume(tid), OK);
    ASSERT_TRUE(eventually([&count]() { return count == 2; }));
    semDelete(sem);  // its semTake fails, and it returns
    ASSERT_TRUE(eventually([tid]() { return finished(tid); }));
}

TEST_F(RealTime, ErrnoAndLogMessages) {
    errnoSet(1234);
    EXPECT_EQ(errnoGet(), 1234);
    EXPECT_GT(logMsg(const_cast<char*>("shim log %d %s\n"), 42, reinterpret_cast<_Vx_usr_arg_t>("ok"), 0, 0, 0, 0), 0);
    const int key = intLock();
    intUnlock(key);
    EXPECT_EQ(taskLock(), OK);
    EXPECT_EQ(taskUnlock(), OK);
}

// ----------------------------------------------------------------------
// Stepped time
// ----------------------------------------------------------------------

namespace {

std::atomic<int> s_tickOfInterrupt{0};
SEM_ID s_steppedSem = SEM_ID_NULL;
WDOG_ID s_steppedWatchdog = WDOG_ID_NULL;

void steppedRoutine(_Vx_usr_arg_t) {
    note("w" + std::to_string(Control::get().tick()));
    wdStart(s_steppedWatchdog, 3, reinterpret_cast<FUNCPTR>(steppedRoutine), 0);
    semGive(s_steppedSem);
}

//! Three tasks and a watchdog, stepped 40 ticks from a pause. What each did, and at which
//! simulation tick.
std::vector<std::string> steppedRun() {
    Control& control = Control::get();
    std::string error;
    // Stepped from the start: what the system clock was granted in real time is issued by
    // the wall clock, at a moment of its own, so the ticker starts again once it is
    control.stopInternalTicker();
    (void)tickGet();
    EXPECT_TRUE(eventually([]() { return Kernel::get().clockPending() == 0; }));
    EXPECT_TRUE(control.execLine(R"({"op": "pause"})", error));
    control.setStepped(true);
    control.startInternalTicker(60);
    taken();
    MSG_Q_ID queue = msgQCreate(2, sizeof(int), MSG_Q_PRIORITY);
    s_steppedSem = semBCreate(SEM_Q_PRIORITY, SEM_EMPTY);
    s_steppedWatchdog = wdCreate();
    std::atomic<bool> quit{false};
    const auto at = []() { return std::to_string(Control::get().tick()); };
    std::function<void()> producer = [&]() {
        for (int i = 0; !quit; i++) {
            taskDelay(2);
            msgQSend(queue, reinterpret_cast<char*>(&i), sizeof i, WAIT_FOREVER, MSG_PRI_NORMAL);
            note("p" + std::to_string(i) + "@" + at());
        }
    };
    std::function<void()> consumer = [&]() {
        int i = 0;
        while (msgQReceive(queue, reinterpret_cast<char*>(&i), sizeof i, WAIT_FOREVER) > 0) {
            // Slow on the host: the tick waits for it all the same
            std::this_thread::sleep_for(std::chrono::milliseconds(3));
            note("c" + std::to_string(i) + "@" + at());
        }
    };
    std::function<void()> handler = [&]() {
        while (semTake(s_steppedSem, WAIT_FOREVER) == OK) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            note("h@" + at());
        }
    };
    const TASK_ID c = spawn("tConsumer", 90, &consumer);
    const TASK_ID p = spawn("tProducer", 100, &producer);
    const TASK_ID h = spawn("tHandler", 80, &handler);
    EXPECT_TRUE(eventually([=]() { return blocked(c) && blocked(h); }));
    wdStart(s_steppedWatchdog, 1, reinterpret_cast<FUNCPTR>(steppedRoutine), 0);
    const std::uint64_t from = control.tick();
    EXPECT_TRUE(control.execLine(R"({"op": "step", "ticks": 40})", error));
    EXPECT_TRUE(eventually([&control, from]() { return control.paused() && control.tick() == from + 40; },
                           std::chrono::seconds(20)));
    std::vector<std::string> log;
    {
        // As an action from outside would: after the last tick has finished (its cycles
        // of the system clock are issued after the step has paused)
        Control::ActionGate gate;
        log = taken();
    }
    // Put the ticks in terms of the run's first one
    for (std::string& entry : log) {
        // "w<tick>", or "<what>@<tick>"
        const std::size_t at = entry.find('@');
        const std::size_t number = at == std::string::npos ? 1 : at + 1;
        entry = entry.substr(0, number) + std::to_string(std::stoull(entry.substr(number)) - from);
    }
    // Done: back to real time, and the tasks end
    quit = true;
    wdCancel(s_steppedWatchdog);
    control.setStepped(false);
    msgQDelete(queue);
    semDelete(s_steppedSem);
    EXPECT_TRUE(eventually([=]() { return finished(c) && finished(h); }));
    EXPECT_TRUE(eventually([=]() { return finished(p) || blocked(p); }));
    taskDelete(p);
    wdDelete(s_steppedWatchdog);
    EXPECT_TRUE(control.execLine(R"({"op": "resume"})", error));
    return log;
}

}  // namespace

TEST_F(RealTime, SteppedTimeRepeatsAndATickWaitsForTheTasksItWakes) {
    const std::vector<std::string> first = steppedRun();
    const std::vector<std::string> second = steppedRun();
    ASSERT_FALSE(first.empty());
    EXPECT_EQ(first, second) << "two runs do the same, in the same order, at the same ticks";
    for (std::size_t i = 0; i < std::min(first.size(), second.size()); i++) {
        if (first[i] != second[i]) {
            ADD_FAILURE() << "entry " << i << ": " << first[i] << " in the first run, " << second[i] << " in the second";
            break;
        }
    }
    EXPECT_EQ(first.size(), second.size());
    // The watchdog's handler task runs in the tick the watchdog fired in, however slowly
    std::string watchdog;
    int handled = 0;
    for (const std::string& entry : first) {
        if (entry[0] == 'w') {
            watchdog = entry.substr(1);
        } else if (entry[0] == 'h') {
            EXPECT_EQ(entry.substr(2), watchdog) << "handled in the tick of the interrupt";
            handled++;
        }
    }
    EXPECT_GE(handled, 12);
    // The consumer gets each message in the tick it was sent in
    for (std::size_t i = 0; i + 1 < first.size(); i++) {
        if (first[i][0] == 'p') {
            const std::string sent = first[i].substr(first[i].find('@'));
            const std::string number = first[i].substr(1, first[i].find('@') - 1);
            bool found = false;
            for (std::size_t j = i + 1; j < first.size() && !found; j++) {
                if (first[j] == "c" + number + sent) {
                    found = true;
                }
            }
            EXPECT_TRUE(found) << first[i] << " was received in the same tick";
        }
    }
    std::string all;
    for (const std::string& entry : first) {
        all += entry + " ";
    }
    std::printf("stepped run: %s\n", all.c_str());
}
