/*
 * tickLib.h: the VxWorks API shim of fprime-wsts. System clock ticks counted so far.
 */
#ifndef WSTS_VXWORKS_TICKLIB_H
#define WSTS_VXWORKS_TICKLIB_H

#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

_Vx_ticks_t tickGet(void);
_Vx_ticks64_t tick64Get(void);

#ifdef __cplusplus
}
#endif

#endif
