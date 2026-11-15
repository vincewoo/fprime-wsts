/*
 * sysLib.h: the VxWorks API shim of fprime-wsts. The system clock.
 *
 * The system clock ticks in simulated time, sysClkRateGet() times a second (60 unless set),
 * from the first simulation tick after the shim first needs it. It stops while the
 * simulation is paused, follows the time scale, and in stepped time its ticks are issued
 * by the stepper, each when the work of the one before is finished.
 */
#ifndef WSTS_VXWORKS_SYSLIB_H
#define WSTS_VXWORKS_SYSLIB_H

#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

int sysClkRateGet(void);
/* 1 to 100000 ticks a second */
STATUS sysClkRateSet(int ticksPerSecond);

#ifdef __cplusplus
}
#endif

#endif
