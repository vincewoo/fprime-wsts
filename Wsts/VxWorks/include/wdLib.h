/*
 * wdLib.h: the VxWorks API shim of fprime-wsts. Watchdog timers.
 *
 * A watchdog routine runs at interrupt level (intContext() is TRUE) on the thread of the
 * system clock, at the system clock tick it is due: in simulated time, so it stops while
 * the simulation is paused and follows the time scale. In stepped time it is part of the
 * simulation tick it falls in.
 */
#ifndef WSTS_VXWORKS_WDLIB_H
#define WSTS_VXWORKS_WDLIB_H

#include <objLib.h>
#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct wdog* WDOG_ID;
#define WDOG_ID_NULL ((WDOG_ID)0)

WDOG_ID wdCreate(void);
STATUS wdDelete(WDOG_ID wdId);
/* A delay of 0 is taken as 1: the routine runs at the next tick */
STATUS wdStart(WDOG_ID wdId, _Vx_ticks_t delay, FUNCPTR pRoutine, _Vx_usr_arg_t parameter);
STATUS wdCancel(WDOG_ID wdId);

#ifdef __cplusplus
}
#endif

#endif
