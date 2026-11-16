module PowerSim {

  @ Simulator of the power monitor that Components.PowerMonitor reads over I2C. An ordinary
  @ F Prime component: it answers the transactions that come through Wsts.BusI2cDevice as
  @ the device at the address the manifest gives, and is commanded and observed through the
  @ ground system like any other. The registers are bytes: a write latches the register
  @ pointer (any following bytes are stored from it), a read returns the registers from it.
  passive component PowerSim {

    # ----------------------------------------------------------------------
    # The I2C device, as the adapter's client
    # ----------------------------------------------------------------------

    @ One transaction to answer. It runs on the flight thread that made it: answer from
    @ inside the call, and keep it short
    sync input port transaction: Wsts.I2cDeviceTransact

    @ Set one register directly (the i2c_reg_set action)
    sync input port poke: Wsts.I2cDevicePoke

    # ----------------------------------------------------------------------
    # Dictionary
    # ----------------------------------------------------------------------

    @ The bus voltage the monitor reports, in millivolts
    sync command SET_VOLTAGE(millivolts: U32) opcode 0x00

    @ The current the monitor reports, in milliamps
    sync command SET_CURRENT(milliamps: U32) opcode 0x01

    @ The bus voltage was set
    event VoltageSet(
                      millivolts: U32 @< The voltage, in millivolts
                    ) \
      severity activity high \
      id 0x00 \
      format "Bus voltage set to {} mV"

    @ The current was set
    event CurrentSet(
                      milliamps: U32 @< The current, in milliamps
                    ) \
      severity activity high \
      id 0x01 \
      format "Current set to {} mA"

    @ The bus voltage, in volts
    telemetry BusVoltage: F32 \
      id 0x00 \
      update on change \
      format "{.1f} V"

    @ The current, in milliamps
    telemetry Current: U32 \
      id 0x01 \
      update on change \
      format "{} mA"

    @ Transactions answered, reads and writes together
    telemetry Transactions: U32 \
      id 0x02 \
      update on change

    command recv port CmdDisp
    command reg port CmdReg
    command resp port CmdStatus
    event port EventOut
    text event port LogText
    telemetry port TlmSend
    time get port timeCaller

  }

}

module PayloadSim {

  @ The adapter that puts PowerSim on the flight I2C bus. The manifest says which endpoint
  @ (devices:) and the options say the address it answers at (addr=0x40)
  instance powerDevice: Wsts.BusI2cDevice base id 0x2F000000

  @ The power monitor itself, commanded through the ground system
  instance powerSim: PowerSim.PowerSim base id 0x2F001000

}
