module SensorLib {

  @ Read a 32-bit register at a byte offset into the device's register block
  port RegRead32(offset: U64) -> U32

  @ Write a 32-bit register
  port RegWrite32(offset: U64, value: U32)

  @ Read a 64-bit register
  port RegRead64(offset: U64) -> U64

  @ Write a 64-bit register
  port RegWrite64(offset: U64, value: U64)

  @ An interrupt of the device. Called in interrupt context: a handler must not block.
  port RegInterrupt(number: U32)

  @ Access to the memory-mapped registers of one device (an FPGA block). Device drivers
  @ read and write registers through an instance of this component, and it delivers the
  @ device's interrupts to them.
  passive component RegisterInterface {

    sync input port read32In: RegRead32

    sync input port write32In: RegWrite32

    sync input port read64In: RegRead64

    sync input port write64In: RegWrite64

    @ Called in interrupt context
    output port interrupt: RegInterrupt

    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut
    event port logOut
    text event port logTextOut
    time get port timeCaller

    @ Read a register and report its value (debugging)
    sync command REG_READ(offset: U32)

    @ Write a register (debugging)
    sync command REG_WRITE(offset: U32, value: U32)

    event RegisterValue(offset: U32, value: U32) \
      severity activity high format "Register {x} reads {x}"

    event RegisterWritten(offset: U32, value: U32) \
      severity activity high format "Register {x} written with {x}"

    event OffsetOutOfRange(offset: U32) \
      severity warning low format "Register offset {x} is outside the device"

  }

}
