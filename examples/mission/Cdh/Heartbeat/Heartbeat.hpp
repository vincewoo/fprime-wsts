// ======================================================================
// \title  Heartbeat.hpp
// \brief  The heartbeat of the command and data handling computer: a watchdog, a
//         semaphore, a message queue and a task of the VxWorks kernel
// ======================================================================
#ifndef CDH_HEARTBEAT_HPP
#define CDH_HEARTBEAT_HPP

#include <Cdh/Heartbeat/HeartbeatComponentAc.hpp>

#include <msgQLib.h>
#include <semLib.h>
#include <taskLib.h>
#include <vxWorks.h>
#include <wdLib.h>

namespace Cdh {

class Heartbeat final : public HeartbeatComponentBase {
  public:
    explicit Heartbeat(const char* compName);
    ~Heartbeat();

    //! Create the kernel objects, spawn the heartbeat task and start the watchdog
    void start(int priority, U32 periodTicks);
    //! Stop the watchdog and the task
    void stop();

  private:
    void run_handler(FwIndexType portNum, U32 context) override;
    void HEARTBEAT_SET_PERIOD_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 ticks) override;

    //! The watchdog routine, at interrupt level: one beat, and the next one armed
    static void beat(_Vx_usr_arg_t self);
    //! The heartbeat task: counts the beats, and takes a new period between two
    static int task(_Vx_usr_arg_t self);

    WDOG_ID m_watchdog = WDOG_ID_NULL;
    SEM_ID m_beat = SEM_ID_NULL;        //!< given by the watchdog, taken by the task
    SEM_ID m_lock = SEM_ID_NULL;        //!< the count, between the task and the rate group
    MSG_Q_ID m_periods = MSG_Q_ID_NULL; //!< new periods, from the command to the task
    TASK_ID m_task = TASK_ID_NULL;
    volatile U32 m_period = 0;          //!< read by the watchdog routine
    U32 m_beats = 0;
    _Vx_ticks_t m_started = 0;
};

}  // namespace Cdh

#endif
