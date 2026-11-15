module Cdh {

  @ The heartbeat of the command and data handling computer, kept the VxWorks way: a
  @ watchdog gives a semaphore every period of the system clock, and a task of its own
  @ counts the beats. Written against the VxWorks kernel API, with no simulation in mind.
  passive component Heartbeat {

    @ Rate group input: report the beats
    sync input port run: Svc.Sched

    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut
    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller

    @ Set the period of the heartbeat, in system clock ticks (1 to 600)
    sync command HEARTBEAT_SET_PERIOD(
      ticks: U32 @< System clock ticks between two beats
    )

    @ The heartbeat task took a new period
    event PeriodSet(ticks: U32) \
      severity activity high format "Heartbeat period set to {} system clock ticks"

    @ A period out of range
    event PeriodRejected(ticks: U32) \
      severity warning low format "Heartbeat period of {} system clock ticks rejected (1 to 600)"

    @ Beats counted since start-up
    telemetry Beats: U32

    @ System clock ticks between two beats
    telemetry PeriodTicks: U32

    @ System clock ticks since the heartbeat started
    telemetry SystemTicks: U32

  }

}
