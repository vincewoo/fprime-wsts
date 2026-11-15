/*
 * taskLib.h: the VxWorks API shim of fprime-wsts. Tasks.
 *
 * A task is an Os::Task of the simulated deployment: in stepped time it takes its turn
 * like flight software's other tasks, in the order the tasks were started. Priority (0 is
 * the highest) decides which of several tasks waiting on one object is served first, and
 * in stepped time the order in which ready tasks run; the host's scheduler does not see it.
 * Stack size and options are accepted and not used.
 */
#ifndef WSTS_VXWORKS_TASKLIB_H
#define WSTS_VXWORKS_TASKLIB_H

#include <objLib.h>
#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef intptr_t TASK_ID;
#define TASK_ID_NULL ((TASK_ID)0)
#define TASK_ID_ERROR ((TASK_ID)-1)

/* Options: accepted, not used */
#define VX_SUPERVISOR_MODE 0x0000
#define VX_UNBREAKABLE 0x0002
#define VX_DEALLOC_STACK 0x0004
#define VX_FP_TASK 0x0008
#define VX_PRIVATE_ENV 0x0080
#define VX_NO_STACK_FILL 0x0100
#define VX_TASK_NOACTIVATE 0x2000

#define S_taskLib_NAME_NOT_FOUND (M_taskLib | 101)
#define S_taskLib_ILLEGAL_PRIORITY (M_taskLib | 104)
#define S_taskLib_ILLEGAL_OPERATION (M_taskLib | 106)

TASK_ID taskSpawn(char* name, int priority, int options, size_t stackSize, FUNCPTR entryPt,
                  _Vx_usr_arg_t arg1, _Vx_usr_arg_t arg2, _Vx_usr_arg_t arg3, _Vx_usr_arg_t arg4,
                  _Vx_usr_arg_t arg5, _Vx_usr_arg_t arg6, _Vx_usr_arg_t arg7, _Vx_usr_arg_t arg8,
                  _Vx_usr_arg_t arg9, _Vx_usr_arg_t arg10);
/* A task that is not started until taskActivate() */
TASK_ID taskCreate(char* name, int priority, int options, size_t stackSize, FUNCPTR entryPt,
                   _Vx_usr_arg_t arg1, _Vx_usr_arg_t arg2, _Vx_usr_arg_t arg3, _Vx_usr_arg_t arg4,
                   _Vx_usr_arg_t arg5, _Vx_usr_arg_t arg6, _Vx_usr_arg_t arg7, _Vx_usr_arg_t arg8,
                   _Vx_usr_arg_t arg9, _Vx_usr_arg_t arg10);
STATUS taskActivate(TASK_ID tid);
/* Another task ends at its next call into the shim; the calling task ends at once */
STATUS taskDelete(TASK_ID tid);
STATUS taskDeleteForce(TASK_ID tid);
void taskExit(int code);
/* Another task is suspended at its next call into the shim */
STATUS taskSuspend(TASK_ID tid);
STATUS taskResume(TASK_ID tid);
/* Simulated time: system clock ticks (sysClkRateGet() a second) */
STATUS taskDelay(_Vx_ticks_t ticks);
TASK_ID taskIdSelf(void);
STATUS taskIdVerify(TASK_ID tid);
char* taskName(TASK_ID tid);
TASK_ID taskNameToId(char* name);
STATUS taskPrioritySet(TASK_ID tid, int newPriority);
STATUS taskPriorityGet(TASK_ID tid, int* pPriority);
/* No preemption to lock out on the host: they return OK and do nothing */
STATUS taskLock(void);
STATUS taskUnlock(void);
STATUS taskSafe(void);
STATUS taskUnsafe(void);

#ifdef __cplusplus
}
#endif

#endif
