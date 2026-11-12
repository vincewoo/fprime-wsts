/*
 * VxWorksCfg.hpp:
 *
 * Project override of the configuration that fprime-vxworks supplies: this vehicle keeps a
 * longer console backlog.
 */

#ifndef VXWORKS_OS_CONFIG_HPP_
#define VXWORKS_OS_CONFIG_HPP_
#include <Fw/Types/BasicTypes.hpp>

namespace Os {
namespace VxWorks {

static constexpr PlatformSizeType MAX_CONSOLE_CAPACITY = 60;            // Max number of messages that can be stored
static constexpr PlatformSizeType MAX_CONSOLE_MESSAGE_BYTE_SIZE = 128;  // Max message size

}  // namespace VxWorks
}  // namespace Os

#endif /* VXWORKS_OS_CONFIG_HPP_ */
