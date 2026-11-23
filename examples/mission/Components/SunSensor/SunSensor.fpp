module Components {

  @ A sun sensor on the payload's SpaceWire network, read over RMAP (ECSS-E-ST-50-52C).
  @ Each tick it reads the sun angle from the sensor's memory; a read that has no reply by
  @ the next tick is reported. Its warnings are throttled, and the throttles are cleared when
  @ a read succeeds again. Its integration time is set with a verified write.
  passive component SunSensor {

    @ 10 Hz rate group: one read of the sun angle a tick
    sync input port run: Svc.Sched

    @ To and from the SpaceWire driver
    import Drv.PassiveBufferDriverClient

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut

    @ Set the sensor's integration time
    sync command SET_INTEGRATION(
      ms: U16 @< Milliseconds
    )

    @ The sensor acknowledged a new integration time
    event IntegrationSet(ms: U16) \
      severity activity high format "Integration time set to {} ms"

    @ The sensor answered a transaction with an error
    event RmapError(transaction: U16, status: U8) \
      severity warning low format "RMAP transaction {} failed with status {}" \
      throttle 10

    @ A read had no reply by the next tick
    event NoReply(transaction: U16) \
      severity warning low format "No reply to RMAP transaction {}" \
      throttle 10

    @ A reply failed its checks: a CRC, its length, its transaction
    event BadReply(transaction: U16) \
      severity warning low format "Reply to RMAP transaction {} failed its checks" \
      throttle 10

    @ The sun angle the sensor measures
    telemetry SunAngle: F32 format "{.3f} deg"

    @ Reads answered
    telemetry Replies: U32

    @ Reads not answered, or answered with an error
    telemetry Errors: U32

  }

}
