module Components {

  @ A stand-in for a science instrument on the payload computer: it takes a sample when
  @ commanded and reports how long it has been running.
  passive component Instrument {

    @ 1 Hz rate group
    sync input port run: Svc.Sched

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut

    @ Take one sample at the given gain
    sync command TAKE_SAMPLE(gain: U8)

    @ A sample was taken
    event SampleTaken(count: U32, gain: U8) \
      severity activity high format "Sample {} taken at gain {}"

    @ Samples taken since start-up
    telemetry Samples: U32

    @ Seconds since start-up
    telemetry Uptime: U32

  }

}
