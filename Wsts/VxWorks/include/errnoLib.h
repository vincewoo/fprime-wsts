/*
 * errnoLib.h: the VxWorks API shim of fprime-wsts. The calling thread's errno.
 */
#ifndef WSTS_VXWORKS_ERRNOLIB_H
#define WSTS_VXWORKS_ERRNOLIB_H

#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

int errnoGet(void);
STATUS errnoSet(int errorValue);

#ifdef __cplusplus
}
#endif

#endif
