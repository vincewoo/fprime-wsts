module LinuxDemo {

  module Default {
    constant QUEUE_SIZE = 10
    constant STACK_SIZE = 64 * 1024
  }

  # ----------------------------------------------------------------------
  # Active component instances
  # ----------------------------------------------------------------------

  instance rateGroup10Hz: Svc.ActiveRateGroup base id 0x10001000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 43

  instance rateGroup1Hz: Svc.ActiveRateGroup base id 0x10002000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 42

  # ----------------------------------------------------------------------
  # Passive component instances
  # ----------------------------------------------------------------------

  instance posixTime: Svc.PosixTime base id 0x10010000

  instance rateGroupDriver: Svc.RateGroupDriver base id 0x10011000

  instance imu: Components.ImuManager base id 0x10012000

  instance sensor: Components.SerialSensor base id 0x10013000

  instance heater: Components.HeaterControl base id 0x10014000

  # ----------------------------------------------------------------------
  # Hardware and operating system interfaces
  # ----------------------------------------------------------------------

  instance linuxTimer: Sse.SimTick base id 0x10020000

  instance comDriver: Drv.TcpClient base id 0x10021000

  instance imuBus: Sse.SimI2c base id 0x10022000

  instance sensorUart: Sse.SimByteStream base id 0x10023000

  instance heaterPin: Sse.SimGpio base id 0x10024000

  instance switchPin: Sse.SimGpio base id 0x10025000

}
