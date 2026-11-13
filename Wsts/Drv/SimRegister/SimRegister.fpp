module Wsts {

  @ Read a 32-bit register at a byte offset into a register block
  port RegisterRead32(offset: U64) -> U32

  @ Write a 32-bit register
  port RegisterWrite32(offset: U64, value: U32)

  @ Read a 64-bit register
  port RegisterRead64(offset: U64) -> U64

  @ Write a 64-bit register
  port RegisterWrite64(offset: U64, value: U64)

  @ An interrupt of a register block. Called on a thread other than the one that reads
  @ and writes the registers, as an interrupt handler is.
  port RegisterInterrupt(number: U32)

  @ Simulation surrogate for a register interface component: the component flight device
  @ drivers read and write memory-mapped registers through, and that delivers the
  @ hardware's interrupts. Accesses are answered by the register block attached to the
  @ WSTS bus endpoint; an interrupt the block raises is sent out of `interrupt`.
  @
  @ A flight register interface component has port types of its own, and FPP connects a
  @ port only to one of the same type. So when this surrogate is named for such a
  @ component, `fprime-wsts generate` writes it again with that component's port names
  @ and types (see the reference, "A surrogate for register interface components").
  passive component SimRegister {

    sync input port read32In: RegisterRead32

    sync input port write32In: RegisterWrite32

    sync input port read64In: RegisterRead64

    sync input port write64In: RegisterWrite64

    output port interrupt: RegisterInterrupt

  }

}
