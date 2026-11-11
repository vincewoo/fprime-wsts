module Payload {

  module Default {
    constant QUEUE_SIZE = 20
    constant STACK_SIZE = 64 * 1024
  }

  # Every ID on this computer is at or above 0x20000000: the Cdh computer's command
  # splitter sends opcodes in that range through the hub.

  # ----------------------------------------------------------------------
  # Active component instances
  # ----------------------------------------------------------------------

  instance rateGroup1Hz: Svc.ActiveRateGroup base id 0x20001000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 42

  instance cmdDisp: Svc.CommandDispatcher base id 0x20002000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 41

  # ----------------------------------------------------------------------
  # Passive component instances
  # ----------------------------------------------------------------------

  instance posixTime: Svc.PosixTime base id 0x20010000

  instance rateGroupDriver: Svc.RateGroupDriver base id 0x20011000

  instance instrument: Components.Instrument base id 0x20012000

  instance textLogger: Svc.PassiveTextLogger base id 0x20013000

  # ----------------------------------------------------------------------
  # The hub to the Cdh computer
  # ----------------------------------------------------------------------

  instance hub: Svc.GenericHub base id 0x20014000

  instance hubAdapter: Drv.ByteStreamBufferAdapter base id 0x20015000

  instance bufferManager: Svc.BufferManager base id 0x20016000

  # ----------------------------------------------------------------------
  # Hardware and operating system interfaces
  # ----------------------------------------------------------------------

  instance linuxTimer: Svc.LinuxTimer base id 0x20020000

  @ Ethernet link to the Cdh computer
  instance hubDriver: Drv.TcpClient base id 0x20021000

}
