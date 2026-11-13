# Simulation surrogate mirroring SensorLib.RegisterInterface. Created by `fprime-wsts scaffold`.

module Sim {

  @ Simulation surrogate for SensorLib.RegisterInterface: same ports and dictionary entries
  passive component SimRegisterInterface {
  
      sync input port read32In: SensorLib.RegRead32
  
      sync input port write32In: SensorLib.RegWrite32
  
      sync input port read64In: SensorLib.RegRead64
  
      sync input port write64In: SensorLib.RegWrite64
  
      @ Called in interrupt context
      output port interrupt: SensorLib.RegInterrupt
  
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
