/*
 * msgQLib.h: the VxWorks API shim of fprime-wsts. Message queues.
 *
 * A message sent while tasks are pended on the queue goes to the first of them (by
 * priority with MSG_Q_PRIORITY, else in the order they pended). MSG_PRI_URGENT puts a
 * message at the head of the queue. A message longer than the receiver's buffer is cut
 * to it.
 */
#ifndef WSTS_VXWORKS_MSGQLIB_H
#define WSTS_VXWORKS_MSGQLIB_H

#include <objLib.h>
#include <vxWorks.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct msg_q* MSG_Q_ID;
#define MSG_Q_ID_NULL ((MSG_Q_ID)0)

#define MSG_Q_FIFO 0x00
#define MSG_Q_PRIORITY 0x01
#define MSG_Q_EVENTSEND_ERR_NOTIFY 0x02
#define MSG_Q_INTERRUPTIBLE 0x04

#define MSG_PRI_NORMAL 0
#define MSG_PRI_URGENT 1

#define S_msgQLib_INVALID_MSG_LENGTH (M_msgQLib | 1)
#define S_msgQLib_NON_ZERO_TIMEOUT_AT_INT_LEVEL (M_msgQLib | 2)
#define S_msgQLib_INVALID_QUEUE_TYPE (M_msgQLib | 3)

MSG_Q_ID msgQCreate(size_t maxMsgs, size_t maxMsgLength, int options);
STATUS msgQDelete(MSG_Q_ID msgQId);
STATUS msgQSend(MSG_Q_ID msgQId, char* buffer, size_t nBytes, _Vx_ticks_t timeout, int priority);
/* The number of bytes received, or ERROR */
ssize_t msgQReceive(MSG_Q_ID msgQId, char* buffer, size_t maxNBytes, _Vx_ticks_t timeout);
int msgQNumMsgs(MSG_Q_ID msgQId);

#ifdef __cplusplus
}
#endif

#endif
