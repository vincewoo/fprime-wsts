module Wsts {

  @ The time source of a simulated deployment. In real time it gives the workstation's
  @ clock, as Svc.PosixTime, Svc.ChronoTime and Svc.OsTime do. In stepped time it gives
  @ simulated time: an epoch plus the ticks taken so far, so that two runs stamp their
  @ events and telemetry alike.
  passive component SimTime {

    import Svc.Time

    @ The epoch of an OsTime-style topology (accepted; the simulation owns the time, so it
    @ changes nothing)
    sync input port setEpoch: Svc.OsTimeEpoch

    @ Port for receiving commands
    command recv port CmdDisp

    @ Port for sending command registration requests
    command reg port CmdReg

    @ Port for sending command responses
    command resp port CmdStatus

    @ Event port
    event port EventOut

    @ Text event port
    text event port LogText

    @ Time get port
    time get port timeCaller

    @ Set the current time (accepted; the simulation owns the time, so it changes nothing)
    sync command SetCurrentTime(seconds_now: U32) opcode 0x00

    @ An error occurred while attempting to set the current time
    event SetCurrentTimeError(
                        status: U32 @< The error status
                        ) \
    severity warning high \
    id 0x00 \
    format "Could not set current time due to RawTime error status {}"

  }

}
