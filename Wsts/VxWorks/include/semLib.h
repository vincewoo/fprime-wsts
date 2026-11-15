/*
 * semLib.h: the VxWorks API shim of fprime-wsts. Binary, counting and mutual-exclusion
 * semaphores.
 *
 * A give with tasks pended hands the semaphore to the first of them (by priority with
 * SEM_Q_PRIORITY, else in the order they pended), as the kernel does. Mutual-exclusion
 * semaphores are recursive and owned by the task that took them. Inversion safety and
 * deletion safety are accepted and not used.
 */
#ifndef WSTS_VXWORKS_SEMLIB_H
#define WSTS_VXWORKS_SEMLIB_H

#include <objLib.h>
#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct semaphore* SEM_ID;
#define SEM_ID_NULL ((SEM_ID)0)

typedef enum { SEM_EMPTY = 0, SEM_FULL = 1 } SEM_B_STATE;

#define SEM_Q_FIFO 0x00
#define SEM_Q_PRIORITY 0x01
#define SEM_DELETE_SAFE 0x04
#define SEM_INVERSION_SAFE 0x08
#define SEM_EVENTSEND_ERR_NOTIFY 0x10
#define SEM_INTERRUPTIBLE 0x20

#define S_semLib_INVALID_STATE (M_semLib | 101)
#define S_semLib_INVALID_OPTION (M_semLib | 102)
#define S_semLib_INVALID_QUEUE_TYPE (M_semLib | 103)
#define S_semLib_INVALID_OPERATION (M_semLib | 104)

SEM_ID semBCreate(int options, SEM_B_STATE initialState);
SEM_ID semCCreate(int options, int initialCount);
SEM_ID semMCreate(int options);
STATUS semDelete(SEM_ID semId);
STATUS semTake(SEM_ID semId, _Vx_ticks_t timeout);
STATUS semGive(SEM_ID semId);
/* Binary and counting semaphores: every task pended on it goes on */
STATUS semFlush(SEM_ID semId);

#ifdef __cplusplus
}
#endif

#endif
