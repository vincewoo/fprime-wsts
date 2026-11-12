#ifndef SIMCOMMON_TYPES_PROFILE_HPP
#define SIMCOMMON_TYPES_PROFILE_HPP

#include <cstring>

#include "SimCommon/Types/MotionEnumAc.hpp"

namespace SimCommon {

//! The motion a profile name stands for
inline Motion motionOf(const char* name) {
    if (name != nullptr && std::strcmp(name, "calm") == 0) {
        return Motion::CALM;
    }
    if (name != nullptr && std::strcmp(name, "rough") == 0) {
        return Motion::ROUGH;
    }
    return Motion::STILL;
}

}  // namespace SimCommon

#endif
