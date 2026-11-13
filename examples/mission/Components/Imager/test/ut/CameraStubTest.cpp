// Unit test of the camera stub that the Imager's own unit tests build on
#include <gtest/gtest.h>

#include "Components/Imager/Camera.hpp"

TEST(CameraStub, FrameIsAGradient) {
    U8 frame[Components::Camera::FRAME_SIZE];
    U32 size = 0;
    ASSERT_EQ(Components::Camera::open(), 0);
    ASSERT_EQ(Components::Camera::capture(frame, sizeof frame, size), 0);
    ASSERT_EQ(size, Components::Camera::FRAME_SIZE);
    ASSERT_EQ(frame[65], 1);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
