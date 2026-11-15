// ======================================================================
// \title  CountedQueueTests.cpp
// \brief  Wsts::CountedQueue as the Os::Queue: F Prime's own queue tests (CommonTests.cpp,
//         QueueRules.cpp of the framework are built into this executable), and what it
//         tells Wsts::Activity
// ======================================================================
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <thread>

#include "Fw/Types/String.hpp"
#include "Os/Queue.hpp"
#include "STest/Random/Random.hpp"
#include "Wsts/Core/Activity.hpp"
#include "Wsts/Os/CountedQueue.hpp"

using Blocking = Os::QueueInterface::BlockingType;
using Status = Os::QueueInterface::Status;
using Wsts::Activity;

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    STest::Random::seed();
    return RUN_ALL_TESTS();
}

namespace {

const U8 MESSAGE[] = {1, 2, 3, 4};

struct Fixture : public ::testing::Test {
    void SetUp() override { Activity::get().reset(); }
    void TearDown() override { Activity::get().reset(); }

    static Status send(Os::Queue& queue) { return queue.send(MESSAGE, sizeof(MESSAGE), 0, Blocking::NONBLOCKING); }
    static Status receive(Os::Queue& queue, Blocking blocking) {
        U8 destination[sizeof(MESSAGE)] = {};
        FwSizeType size = 0;
        FwQueuePriorityType priority = 0;
        return queue.receive(destination, sizeof(destination), blocking, size, priority);
    }
};

}  // namespace

TEST_F(Fixture, TheQueueOfTheDeploymentIsTheCountingOne) {
    Os::Queue queue;
    ASSERT_EQ(queue.create(0, Fw::String("chosen"), 2, sizeof(MESSAGE)), Status::OP_OK);
    EXPECT_TRUE(Activity::get().counting()) << "Os::Queue is not Wsts::CountedQueue: CHOOSES_IMPLEMENTATIONS did not take";
    queue.teardown();
}

TEST_F(Fixture, AMessageForATaskIsWorkFromSendUntilItsHandlerReturns) {
    Os::Queue queue;
    ASSERT_EQ(queue.create(0, Fw::String("task"), 4, sizeof(MESSAGE)), Status::OP_OK);
    std::atomic<int> handled{0};
    std::atomic<bool> release{false};
    // An active component's task: wait for a message, handle it, come back for the next
    std::thread task([&]() {
        for (int i = 0; i < 2; i++) {
            ASSERT_EQ(receive(queue, Blocking::BLOCKING), Status::OP_OK);
            handled++;
            while (!release.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            release.store(false);
        }
        // The last message is finished when the task asks again: here it would block
        EXPECT_EQ(receive(queue, Blocking::NONBLOCKING), Status::EMPTY);
    });
    EXPECT_TRUE(Activity::get().waitQuiescent(std::chrono::milliseconds(0)));
    ASSERT_EQ(send(queue), Status::OP_OK);
    ASSERT_EQ(send(queue), Status::OP_OK);
    EXPECT_EQ(Activity::get().total(), 2u);
    while (handled.load() < 1) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    EXPECT_EQ(Activity::get().pending(), 2u) << "one waiting, one in its handler";
    EXPECT_EQ(Activity::get().holders(), "task: 2");
    EXPECT_FALSE(Activity::get().waitQuiescent(std::chrono::milliseconds(20)));
    release.store(true);
    while (handled.load() < 2) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    EXPECT_EQ(Activity::get().pending(), 1u) << "the first is finished: the task came back for the second";
    release.store(true);
    EXPECT_TRUE(Activity::get().waitQuiescent(std::chrono::milliseconds(2000)));
    task.join();
    EXPECT_EQ(Activity::get().holders(), "");
    queue.teardown();
}

TEST_F(Fixture, AMessageOnAPolledQueueIsWorkOnlyWhileItIsHandled) {
    // A queued component: its messages wait until whoever dispatches it (a rate group, on
    // a later tick) takes them. Waiting there, they hold nothing up.
    Os::Queue queue;
    ASSERT_EQ(queue.create(0, Fw::String("polled"), 4, sizeof(MESSAGE)), Status::OP_OK);
    ASSERT_EQ(send(queue), Status::OP_OK);
    EXPECT_EQ(Activity::get().pending(), 1u) << "not yet known to be polled";
    ASSERT_EQ(receive(queue, Blocking::NONBLOCKING), Status::OP_OK);
    EXPECT_EQ(Activity::get().pending(), 1u) << "being handled";
    ASSERT_EQ(send(queue), Status::OP_OK);
    ASSERT_EQ(send(queue), Status::OP_OK);
    EXPECT_EQ(Activity::get().pending(), 1u) << "two more wait: still only the one in its handler";
    ASSERT_EQ(receive(queue, Blocking::NONBLOCKING), Status::OP_OK);
    EXPECT_EQ(Activity::get().pending(), 1u) << "the first is finished, the second is being handled";
    ASSERT_EQ(receive(queue, Blocking::NONBLOCKING), Status::OP_OK);
    ASSERT_EQ(receive(queue, Blocking::NONBLOCKING), Status::EMPTY);
    EXPECT_EQ(Activity::get().pending(), 0u);
    ASSERT_EQ(send(queue), Status::OP_OK);
    EXPECT_TRUE(Activity::get().waitQuiescent(std::chrono::milliseconds(0))) << "left for the next dispatch";
    queue.teardown();
}

TEST_F(Fixture, AMessageThatIsRefusedIsNotWork) {
    Os::Queue queue;
    ASSERT_EQ(queue.create(0, Fw::String("full"), 1, sizeof(MESSAGE)), Status::OP_OK);
    ASSERT_EQ(send(queue), Status::OP_OK);
    ASSERT_EQ(send(queue), Status::FULL);
    EXPECT_EQ(Activity::get().pending(), 1u);
    queue.teardown();
}

TEST_F(Fixture, ADispatchInsideAHandlerKeepsBothApart) {
    // A rate group's task, inside the handler of its own cycle message, dispatches a
    // queued component: finishing the inner message must not finish the outer one
    Os::Queue outer;
    Os::Queue inner;
    ASSERT_EQ(outer.create(0, Fw::String("rateGroup"), 2, sizeof(MESSAGE)), Status::OP_OK);
    ASSERT_EQ(inner.create(1, Fw::String("queued"), 2, sizeof(MESSAGE)), Status::OP_OK);
    ASSERT_EQ(receive(inner, Blocking::NONBLOCKING), Status::EMPTY);  // known to be polled
    ASSERT_EQ(send(inner), Status::OP_OK);
    ASSERT_EQ(send(outer), Status::OP_OK);
    ASSERT_EQ(receive(outer, Blocking::BLOCKING), Status::OP_OK);
    ASSERT_EQ(receive(inner, Blocking::NONBLOCKING), Status::OP_OK);
    EXPECT_EQ(Activity::get().pending(), 2u);
    ASSERT_EQ(receive(inner, Blocking::NONBLOCKING), Status::EMPTY);
    EXPECT_EQ(Activity::get().pending(), 1u) << "the cycle is still being handled";
    ASSERT_EQ(receive(outer, Blocking::NONBLOCKING), Status::EMPTY);
    EXPECT_EQ(Activity::get().pending(), 0u);
    outer.teardown();
    inner.teardown();
}
