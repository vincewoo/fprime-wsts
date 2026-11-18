// ======================================================================
// \title  SimTaskTests.cpp
// \brief  What Wsts::SimTask adds to the POSIX task: a task takes its turn before its
//         routine runs. (The framework's own task tests are built against it as
//         Wsts_Os_SimTask_ut; they count the tasks of their process, so these tests have
//         an executable of their own.)
// ======================================================================
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>
#include <vector>

#include "Fw/Types/String.hpp"
#include "Os/Task.hpp"
#include "Wsts/Core/Activity.hpp"

using Wsts::Activity;

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}

namespace {

std::mutex s_orderMutex;
std::vector<int> s_order;
std::atomic<int> s_running{0};
std::atomic<int> s_mostAtOnce{0};

void routine(void* argument) {
    const int now = ++s_running;
    int most = s_mostAtOnce.load();
    while (now > most && !s_mostAtOnce.compare_exchange_weak(most, now)) {
    }
    {
        std::lock_guard<std::mutex> lock(s_orderMutex);
        s_order.push_back(static_cast<int>(reinterpret_cast<std::intptr_t>(argument)));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    --s_running;
}

void startAll(Os::Task* tasks, int count) {
    for (int i = 0; i < count; i++) {
        const Os::Task::Arguments arguments(Fw::String("turn"), routine, reinterpret_cast<void*>(static_cast<std::intptr_t>(i)));
        ASSERT_EQ(tasks[i].start(arguments), Os::Task::Status::OP_OK);
    }
}

}  // namespace

TEST(SimTask, TasksRunOneAtATimeAndInTheOrderTheyWereStarted) {
    Activity::get().reset();
    Activity::get().setExclusive(true);  // stepped time
    Activity::get().holdTasks(true);     // the deployment is still starting up
    s_order.clear();
    s_mostAtOnce.store(0);
    Os::Task tasks[6];
    startAll(tasks, 6);
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    EXPECT_TRUE(s_order.empty()) << "no task runs before start-up is over";
    Activity::get().holdTasks(false);
    for (Os::Task& task : tasks) {
        ASSERT_EQ(task.join(), Os::Task::Status::OP_OK);
    }
    EXPECT_EQ(s_order, (std::vector<int>{0, 1, 2, 3, 4, 5}));
    EXPECT_EQ(s_mostAtOnce.load(), 1);
    EXPECT_FALSE(Activity::get().hasTurn());
    EXPECT_TRUE(Activity::get().enter()) << "the last task gave the turn up when it ended";
    Activity::get().leave();
    Activity::get().reset();
}

TEST(SimTask, InRealTimeTasksRunAsTheHostSchedulesThem) {
    Activity::get().reset();
    s_order.clear();
    s_mostAtOnce.store(0);
    Os::Task tasks[6];
    startAll(tasks, 6);
    for (Os::Task& task : tasks) {
        ASSERT_EQ(task.join(), Os::Task::Status::OP_OK);
    }
    EXPECT_EQ(s_order.size(), 6u);
    EXPECT_GT(s_mostAtOnce.load(), 1) << "six tasks of 5 ms each were not held apart";
}

// ----------------------------------------------------------------------
// A pin to a core the host does not have
// ----------------------------------------------------------------------

TEST(SimTask, ATaskPinnedToACoreTheHostLacksStartsUnpinned) {
    Activity::get().reset();
    s_order.clear();
    Os::Task task;
    // Far above any host's cores, but below the size of a cpu set (1024), so the pin itself can be built
    const Os::Task::Arguments arguments(Fw::String("pinned"), routine, reinterpret_cast<void*>(7),
                                        Os::Task::TASK_PRIORITY_DEFAULT, Os::Task::TASK_DEFAULT, 1000);
    ASSERT_EQ(task.start(arguments), Os::Task::Status::OP_OK);
    ASSERT_EQ(task.join(), Os::Task::Status::OP_OK);
    EXPECT_EQ(s_order, (std::vector<int>{7}));
}

// ----------------------------------------------------------------------
// Os::Task::delay does not keep the turn
// ----------------------------------------------------------------------

namespace {

std::atomic<bool> s_sleeping{false};
std::atomic<bool> s_sleeperBack{false};
std::atomic<bool> s_hadTurnAfter{false};

void sleeper(void*) {
    // A socket driver's reconnect loop: it polls with delay()
    s_sleeping.store(true);
    (void)Os::Task::delay(Fw::TimeInterval(0, 300000));  // 300 ms
    s_hadTurnAfter.store(Activity::get().hasTurn());
    s_sleeperBack.store(true);
}

}  // namespace

TEST(SimTask, ASleepingTaskDoesNotKeepTheTurn) {
    Activity::get().reset();
    Activity::get().setExclusive(true);
    Activity::get().holdTasks(false);
    s_sleeping.store(false);
    s_sleeperBack.store(false);
    s_hadTurnAfter.store(false);
    Os::Task task;
    const Os::Task::Arguments arguments(Fw::String("sleeper"), sleeper, nullptr);
    ASSERT_EQ(task.start(arguments), Os::Task::Status::OP_OK);
    while (!s_sleeping.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(30));  // it is in delay() by now
    ASSERT_FALSE(s_sleeperBack.load());
    // The test's thread, a thread of the simulation like the tick source, takes the turn
    // while the task sleeps: with the old delay it would wait the whole 300 ms
    const auto before = std::chrono::steady_clock::now();
    ASSERT_TRUE(Activity::get().enter());
    const auto waited = std::chrono::steady_clock::now() - before;
    EXPECT_LT(std::chrono::duration_cast<std::chrono::milliseconds>(waited).count(), 100);
    EXPECT_FALSE(s_sleeperBack.load()) << "the turn was taken while the task was still asleep";
    Activity::get().leave();
    ASSERT_EQ(task.join(), Os::Task::Status::OP_OK);
    EXPECT_TRUE(s_sleeperBack.load());
    EXPECT_TRUE(s_hadTurnAfter.load()) << "the task has the turn again when delay() returns";
    EXPECT_FALSE(Activity::get().hasTurn());
    Activity::get().reset();
}

TEST(SimTask, InRealTimeDelayIsThePosixOne) {
    Activity::get().reset();  // not one at a time
    s_sleeping.store(false);
    s_sleeperBack.store(false);
    Os::Task task;
    const Os::Task::Arguments arguments(Fw::String("sleeper"), sleeper, nullptr);
    const auto before = std::chrono::steady_clock::now();
    ASSERT_EQ(task.start(arguments), Os::Task::Status::OP_OK);
    ASSERT_EQ(task.join(), Os::Task::Status::OP_OK);
    const auto took = std::chrono::steady_clock::now() - before;
    EXPECT_GE(std::chrono::duration_cast<std::chrono::milliseconds>(took).count(), 290);
    EXPECT_TRUE(s_sleeperBack.load());
}
