// ======================================================================
// The camera through the vendor's SDK. Built for the flight board only: the SDK's headers
// and libraries are in that board's sysroot.
// ======================================================================
#include "Components/Imager/Camera.hpp"

#include <camcodec.h>
#include <camsdk/camera.h>

namespace Components {
namespace Camera {

namespace {
camsdk_handle_t s_camera = nullptr;
}

I32 open() {
    return camsdk_open(CAMSDK_PORT_CSI0, &s_camera);
}

I32 setExposure(U32 ticks) {
    return camsdk_set(s_camera, CAMSDK_EXPOSURE, ticks);
}

I32 capture(U8* frame, U32 capacity, U32& size) {
    camsdk_raw_frame_t raw;
    const I32 status = camsdk_grab(s_camera, &raw);
    if (status != 0) {
        return status;
    }
    // The sensor delivers packed 10-bit pixels: the codec unpacks them to 8 bits
    size = camcodec_unpack8(raw.data, raw.size, frame, capacity);
    return camsdk_release(s_camera, &raw);
}

I32 selfTest() {
    return camsdk_pattern_test(s_camera);
}

}  // namespace Camera
}  // namespace Components
