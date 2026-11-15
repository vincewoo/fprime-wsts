/*
 * logLib.h: the VxWorks API shim of fprime-wsts. Messages to the log, written to the
 * deployment's standard error (wsts-logs/sim.log), at once.
 */
#ifndef WSTS_VXWORKS_LOGLIB_H
#define WSTS_VXWORKS_LOGLIB_H

#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

int logMsg(char* fmt, _Vx_usr_arg_t arg1, _Vx_usr_arg_t arg2, _Vx_usr_arg_t arg3, _Vx_usr_arg_t arg4,
           _Vx_usr_arg_t arg5, _Vx_usr_arg_t arg6);

#ifdef __cplusplus
}
#endif

#endif
