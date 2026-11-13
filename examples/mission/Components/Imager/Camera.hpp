// ======================================================================
// The payload camera, as the Imager component sees it.
//
// Two implementations: CameraSdk.cpp drives the camera through the vendor's SDK and is
// what flies; CameraStub.cpp is for unit tests and returns a fixed frame.
// ======================================================================
#ifndef COMPONENTS_IMAGER_CAMERA_HPP
#define COMPONENTS_IMAGER_CAMERA_HPP

#include <Fw/FPrimeBasicTypes.hpp>

namespace Components {
namespace Camera {

constexpr U32 FRAME_SIZE = 64 * 48;

//! Open the camera. Returns 0, or the SDK's error code.
I32 open();

//! Set the exposure, in sensor ticks
I32 setExposure(U32 ticks);

//! Capture one frame of FRAME_SIZE bytes into `frame`
I32 capture(U8* frame, U32 capacity, U32& size);

//! Run the sensor's built-in pattern test. Only with the SDK: the stub has no sensor.
I32 selfTest();

}  // namespace Camera
}  // namespace Components

#endif
