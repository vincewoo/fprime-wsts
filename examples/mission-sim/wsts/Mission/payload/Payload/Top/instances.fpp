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

  instance rateGroup10Hz: Svc.ActiveRateGroup base id 0x20001000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 43

  instance rateGroup1Hz: Svc.ActiveRateGroup base id 0x20002000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 42

  instance cmdDisp: Svc.CommandDispatcher base id 0x20003000 \
    queue size Default.QUEUE_SIZE \
    stack size Default.STACK_SIZE \
    priority 41

  # ----------------------------------------------------------------------
  # Passive component instances
  # ----------------------------------------------------------------------

  instance posixTime: Svc.PosixTime base id 0x20010000

  instance rateGroupDriver: Svc.RateGroupDriver base id 0x20011000

  instance textLogger: Svc.PassiveTextLogger base id 0x20012000

  instance instrument: Components.Instrument base id 0x20013000

  instance imu: Components.ImuManager base id 0x20014000

  instance power: Components.PowerMonitor base id 0x20015000

  @ The same serial temperature sensor as on the Cdh computer, from sensor-lib
  instance sensor: SensorLib.SerialSensor base id 0x20016000

  instance heater: Components.HeaterControl base id 0x20017000

  instance adc: Components.Adc base id 0x20018000

  @ The payload camera, through its vendor's SDK
  instance imager: Components.Imager base id 0x20019000

  # ----------------------------------------------------------------------
  # The hub to the Cdh computer
  # ----------------------------------------------------------------------

  instance hub: Svc.GenericHub base id 0x20020000

  instance hubAdapter: Drv.ByteStreamBufferAdapter base id 0x20021000

  instance bufferManager: Svc.BufferManager base id 0x20022000

  # ----------------------------------------------------------------------
  # Hardware and operating system interfaces
  # ----------------------------------------------------------------------

  instance linuxTimer: Wsts.SimTick base id 0x20030000

  @ Ethernet link to the Cdh computer
  instance hubDriver: Wsts.SimByteStream base id 0x20031000

  @ I2C bus with the IMU (0x68) and the power monitor (0x40)
  instance i2cBus: Wsts.SimI2c base id 0x20032000

  instance sensorUart: Wsts.SimByteStream base id 0x20033000

  instance heaterPin: Sim.SimLinuxGpio base id 0x20034000

  instance switchPin: Sim.SimLinuxGpio base id 0x20035000

  @ SPI bus with the instrument's converter
  instance spiBus: Wsts.SimSpi base id 0x20036000

}
