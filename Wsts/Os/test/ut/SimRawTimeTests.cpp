// ======================================================================
// \title  SimRawTimeTests.cpp
// \brief  Wsts::SimRawTime as the Os::RawTime: built with the framework's own raw time
//         tests (CommonTests.cpp, RawTimeRules.cpp, PosixRawTimeTests.cpp, which has
//         main()), and what it adds: simulated time in stepped time
// ======================================================================
#include <gtest/gtest.h>

#include "Fw/Time/TimeInterval.hpp"
#include "Os/RawTime.hpp"
#include "Wsts/Core/Control.hpp"

using Wsts::Control;

TEST(SimRawTime, SteppedTimeStandsStillWithinATickAndMovesByThePeriod) {
    Control& control = Control::get();
    control.reset();
    control.setTickPeriod(50000000);  // 20 Hz
    control.setStepped(true);
    Os::RawTime before;
    Os::RawTime same;
    ASSERT_EQ(before.now(), Os::RawTime::Status::OP_OK);
    ASSERT_EQ(same.now(), Os::RawTime::Status::OP_OK);
    U32 microseconds = 1;
    ASSERT_EQ(same.getDiffUsec(before, microseconds), Os::RawTime::Status::OP_OK);
    EXPECT_EQ(microseconds, 0u) << "a rate group that times its cycle measures nothing, in every run";
    control.onTick();
    control.onTick();
    control.onTick();
    Os::RawTime after;
    ASSERT_EQ(after.now(), Os::RawTime::Status::OP_OK);
    ASSERT_EQ(after.getDiffUsec(before, microseconds), Os::RawTime::Status::OP_OK);
    EXPECT_EQ(microseconds, 150000u) << "three ticks of 50 ms";
    // In real time it is the host's clock again
    control.setStepped(false);
    Os::RawTime host;
    ASSERT_EQ(host.now(), Os::RawTime::Status::OP_OK);
    Fw::TimeInterval interval;
    ASSERT_EQ(host.getTimeInterval(after, interval), Os::RawTime::Status::OP_OK);
    EXPECT_GT(interval.getSeconds(), 1000000u) << "the host's clock is far from the simulated epoch plus 150 ms";
    control.reset();
}
