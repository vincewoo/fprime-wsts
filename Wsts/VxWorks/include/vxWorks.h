/*
 * vxWorks.h: the VxWorks API shim of fprime-wsts (docs/vxworks-shim.md)
 *
 * Basic types and constants of the VxWorks kernel API, for project code that is compiled
 * unchanged in a simulated deployment. The functions are implemented on POSIX threads by
 * Wsts/VxWorks, and take part in stepped time. Only the subset documented there exists:
 * code that uses anything else does not compile, which is the point at which to guard it
 * (#ifndef WSTS_SIM) or to write a surrogate.
 */
#ifndef WSTS_VXWORKS_VXWORKS_H
#define WSTS_VXWORKS_VXWORKS_H

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

/* Defined by every header of the shim: code can tell the shim from the real kernel */
#define WSTS_VXWORKS_SHIM 1

#ifdef __cplusplus
extern "C" {
#endif

typedef int STATUS;
typedef int BOOL;
typedef char INT8;
typedef short INT16;
typedef int INT32;
typedef long long INT64;
typedef unsigned char UINT8;
typedef unsigned short UINT16;
typedef unsigned int UINT32;
typedef unsigned long long UINT64;
typedef unsigned char UCHAR;
typedef unsigned short USHORT;
typedef unsigned int UINT;
typedef unsigned long ULONG;

/* An argument passed through the kernel (task arguments, watchdog parameter) */
typedef intptr_t _Vx_usr_arg_t;
/* System clock ticks */
typedef unsigned int _Vx_ticks_t;
typedef unsigned long long _Vx_ticks64_t;
typedef int _Vx_freq_t;

#ifdef __cplusplus
typedef int (*FUNCPTR)(...);
typedef void (*VOIDFUNCPTR)(...);
#else
typedef int (*FUNCPTR)();
typedef void (*VOIDFUNCPTR)();
#endif

/* Enumerators, not macros, as F Prime's VxWorks platform (fprime-vxworks) makes them: F
 * Prime's own headers use these names for enumerators of their own. The VXWORKS_ names are
 * that platform's. */
enum WstsVxWorksConstants {
    VXWORKS_ERROR = -1,
    VXWORKS_OK = 0,
    VXWORKS_NO_WAIT = 0,
    VXWORKS_READ = 0,
    VXWORKS_NONE = -1
};
#undef OK
#undef ERROR
#undef NO_WAIT
#undef READ
#undef NONE
enum { ERROR = VXWORKS_ERROR, OK = VXWORKS_OK, NO_WAIT = VXWORKS_NO_WAIT, READ = VXWORKS_READ, NONE = VXWORKS_NONE };

#ifndef TRUE
#define TRUE 1
#endif
#ifndef FALSE
#define FALSE 0
#endif
#ifndef NULL
#define NULL 0
#endif

/* Time-outs, in system clock ticks (and NO_WAIT, above) */
#define WAIT_FOREVER (-1)

/* errno values are a module number in the upper half and a code in the lower */
#define M_errno (0 << 16)
#define M_taskLib (3 << 16)
#define M_semLib (22 << 16)
#define M_objLib (61 << 16)
#define M_msgQLib (65 << 16)

#ifdef __cplusplus
}
#endif

#endif
