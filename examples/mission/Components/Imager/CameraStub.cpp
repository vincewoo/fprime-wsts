// ======================================================================
// A stub of the camera for unit tests: every frame is the same gradient.
// ======================================================================
#include "Components/Imager/Camera.hpp"

namespace Components {
namespace Camera {

I32 open() {
    return 0;
}

I32 setExposure(U32 ticks) {
    (void)ticks;
    return 0;
}

I32 capture(U8* frame, U32 capacity, U32& size) {
    size = (capacity < FRAME_SIZE) ? capacity : FRAME_SIZE;
    for (U32 i = 0; i < size; i++) {
        frame[i] = static_cast<U8>(i % 64);
    }
    return 0;
}

}  // namespace Camera
}  // namespace Components
