module Cdh {

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

  instance linuxTimer: Svc.LinuxTimer base id 0x10020000

  @ Link to the ground system
  instance comDriver: Drv.TcpClient base id 0x10021000

  @ Ethernet link to the payload computer, which connects to this one
  instance hubDriver: Drv.TcpServer base id 0x10022000

}
