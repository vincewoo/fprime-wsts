// ======================================================================
// \title  Heartbeat.cpp
// \brief  The heartbeat of the command and data handling computer
// ======================================================================
#include <Cdh/Heartbeat/Heartbeat.hpp>

#include <Fw/Types/Assert.hpp>

#include <logLib.h>
#include <tickLib.h>

namespace Cdh {

namespace {
constexpr U32 MAX_PERIOD = 600;
}

Heartbeat::Heartbeat(const char* compName) : HeartbeatComponentBase(compName) {}

Heartbeat::~Heartbeat() {
    this->stop();
}

void Heartbeat::start(int priority, U32 periodTicks) {
    FW_ASSERT(periodTicks > 0 && periodTicks <= MAX_PERIOD, static_cast<FwAssertArgType>(periodTicks));
    m_period = periodTicks;
    m_beat = semBCreate(SEM_Q_PRIORITY, SEM_EMPTY);
    m_lock = semMCreate(SEM_Q_PRIORITY | SEM_INVERSION_SAFE);
    m_periods = msgQCreate(4, sizeof(U32), MSG_Q_FIFO);
    m_watchdog = wdCreate();
    FW_ASSERT(m_beat != SEM_ID_NULL && m_lock != SEM_ID_NULL && m_periods != MSG_Q_ID_NULL &&
              m_watchdog != WDOG_ID_NULL);
    m_started = tickGet();
    m_task = taskSpawn(const_cast<char*>("tHeartbeat"), priority, VX_FP_TASK, 8192, reinterpret_cast<FUNCPTR>(task),
                       reinterpret_cast<_Vx_usr_arg_t>(this), 0, 0, 0, 0, 0, 0, 0, 0, 0);
    FW_ASSERT(m_task != TASK_ID_ERROR);
    const STATUS status = wdStart(m_watchdog, static_cast<_Vx_ticks_t>(m_period), reinterpret_cast<FUNCPTR>(beat),
                                  reinterpret_cast<_Vx_usr_arg_t>(this));
    FW_ASSERT(status == OK);
}

void Heartbeat::stop() {
    if (m_watchdog != WDOG_ID_NULL) {
        (void)wdCancel(m_watchdog);
    }
    if (m_task != TASK_ID_NULL) {
        (void)taskDelete(m_task);
        m_task = TASK_ID_NULL;
    }
}

void Heartbeat::beat(_Vx_usr_arg_t self) {
    Heartbeat* heartbeat = reinterpret_cast<Heartbeat*>(self);
    (void)semGive(heartbeat->m_beat);
    (void)wdStart(heartbeat->m_watchdog, static_cast<_Vx_ticks_t>(heartbeat->m_period),
                  reinterpret_cast<FUNCPTR>(beat), self);
}

int Heartbeat::task(_Vx_usr_arg_t self) {
    Heartbeat* heartbeat = reinterpret_cast<Heartbeat*>(self);
    for (;;) {
        if (semTake(heartbeat->m_beat, WAIT_FOREVER) != OK) {
            return ERROR;
        }
        (void)semTake(heartbeat->m_lock, WAIT_FOREVER);
        heartbeat->m_beats++;
        (void)semGive(heartbeat->m_lock);
        // A new period, if one was commanded: from the next beat on
        U32 period = 0;
        if (msgQReceive(heartbeat->m_periods, reinterpret_cast<char*>(&period), sizeof period, NO_WAIT) ==
            static_cast<ssize_t>(sizeof period)) {
            heartbeat->m_period = period;
            (void)logMsg(const_cast<char*>("heartbeat period now %u ticks\n"), static_cast<_Vx_usr_arg_t>(period),
                         0, 0, 0, 0, 0);
            heartbeat->log_ACTIVITY_HI_PeriodSet(period);
        }
    }
}

void Heartbeat::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    (void)semTake(m_lock, WAIT_FOREVER);
    const U32 beats = m_beats;
    (void)semGive(m_lock);
    this->tlmWrite_Beats(beats);
    this->tlmWrite_PeriodTicks(m_period);
    this->tlmWrite_SystemTicks(static_cast<U32>(tickGet() - m_started));
}

void Heartbeat::HEARTBEAT_SET_PERIOD_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 ticks) {
    if (ticks == 0 || ticks > MAX_PERIOD) {
        this->log_WARNING_LO_PeriodRejected(ticks);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    const STATUS status = msgQSend(m_periods, reinterpret_cast<char*>(&ticks), sizeof ticks, NO_WAIT, MSG_PRI_NORMAL);
    this->cmdResponse_out(opCode, cmdSeq, status == OK ? Fw::CmdResponse::OK : Fw::CmdResponse::EXECUTION_ERROR);
}

}  // namespace Cdh
