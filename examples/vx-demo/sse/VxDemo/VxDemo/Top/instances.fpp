module VxDemo {

  module Default {
    constant QUEUE_SIZE = 10
    constant STACK_SIZE = 64 * 1024
  }

  instance rateGroup10Hz: Svc.ActiveRateGroup base id 0x20001000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 43

  instance rateGroup1Hz: Svc.ActiveRateGroup base id 0x20002000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 42

  instance chronoTime: Svc.ChronoTime base id 0x20010000

  instance rateGroupDriver: Svc.RateGroupDriver base id 0x20011000

  instance sensor: Components.SerialSensor base id 0x20012000

  instance comDriver: Drv.TcpClient base id 0x20020000

  # ----------------------------------------------------------------------
  # VxWorks-specific instances
  # ----------------------------------------------------------------------

  @ Watchdog timer that drives the rate groups from interrupt context
  instance vxTimer: Sse.SimTick base id 0x20021000

  @ Serial port the temperature sensor is on
  instance sensorUart: Sse.SimByteStream base id 0x20022000

}
