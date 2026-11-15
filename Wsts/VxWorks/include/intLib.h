/*
 * intLib.h: the VxWorks API shim of fprime-wsts. Interrupt level.
 *
 * Watchdog routines are the only interrupt level there is. intLock() keeps them out (and
 * other tasks that call intLock()) until the matching intUnlock(); it does not stop tasks.
 */
#ifndef WSTS_VXWORKS_INTLIB_H
#define WSTS_VXWORKS_INTLIB_H

#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

BOOL intContext(void);
int intLock(void);
void intUnlock(int lockKey);

#ifdef __cplusplus
}
#endif

#endif
