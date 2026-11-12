module Cdh {

  module Default {
    constant QUEUE_SIZE = 10
    constant STACK_SIZE = 64 * 1024
  }

  # Every ID on this computer is below 0x20000000: the command splitter sends opcodes from
  # there up to the payload computer. CdhCore and the SensorLink subtopology of sensor-lib
  # are placed by the project's configuration (config/project).

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

  instance chronoTime: Svc.ChronoTime base id 0x10010000

  instance rateGroupDriver: Svc.RateGroupDriver base id 0x10011000

  # ----------------------------------------------------------------------
  # The hub to the payload computer
  # ----------------------------------------------------------------------

  @ Sends commands for the payload computer (opcodes from 0x20000000) to the hub
  instance cmdSplitter: Svc.CmdSplitter base id 0x10012000

  instance hub: Svc.GenericHub base id 0x10013000

  instance hubAdapter: Drv.ByteStreamBufferAdapter base id 0x10014000

  # ----------------------------------------------------------------------
  # Hardware and operating system interfaces
  # ----------------------------------------------------------------------

  @ Link to the ground system
  instance comDriver: Drv.TcpClient base id 0x10020000

  @ Ethernet link to the payload computer, which connects to this one
  instance hubDriver: Drv.TcpServer base id 0x10021000

  @ Watchdog timer that drives the rate groups from interrupt context
  instance vxTimer: VxWorksSvc.VxWatchDogTimer base id 0x10022000

  # The serial sensor's driver is not here: it is inside the SensorLink subtopology of
  # lib/sensor-lib

}
