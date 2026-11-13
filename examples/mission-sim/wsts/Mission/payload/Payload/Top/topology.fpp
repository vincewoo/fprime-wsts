module Payload {

  enum Ports_RateGroups {
    rateGroup10Hz
    rateGroup1Hz
  }

  deployment topology Payload {

    # WSTS instances (generated)
    instance wstsSetup
    instance wstsControl


    # ----------------------------------------------------------------------
    # Instances
    # ----------------------------------------------------------------------

    instance rateGroup10Hz
    instance rateGroup1Hz
    instance cmdDisp
    instance posixTime
    instance rateGroupDriver
    instance textLogger
    instance instrument
    instance imu
    instance power
    instance sensor
    instance heater
    instance adc
    instance imager
    instance hub
    instance hubAdapter
    instance bufferManager
    instance linuxTimer
    instance hubDriver
    instance i2cBus
    instance sensorUart
    instance heaterPin
    instance switchPin
    instance spiBus

    # ----------------------------------------------------------------------
    # Pattern graph specifiers
    # ----------------------------------------------------------------------

    # This computer has no ground link of its own: its events and telemetry go through
    # the hub to the Cdh computer, and its commands arrive the same way.
    command connections instance cmdDisp
    event connections instance hub
    telemetry connections instance hub
    text event connections instance textLogger
    time connections instance posixTime

    # ----------------------------------------------------------------------
    # Direct graph specifiers
    # ----------------------------------------------------------------------

    connections RateGroups {
      linuxTimer.CycleOut -> rateGroupDriver.CycleIn

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup10Hz] -> rateGroup10Hz.CycleIn
      rateGroup10Hz.RateGroupMemberOut[0] -> imu.run

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup1Hz] -> rateGroup1Hz.CycleIn
      rateGroup1Hz.RateGroupMemberOut[0] -> instrument.run
      rateGroup1Hz.RateGroupMemberOut[1] -> cmdDisp.run
      rateGroup1Hz.RateGroupMemberOut[2] -> heater.run
      rateGroup1Hz.RateGroupMemberOut[3] -> power.run
      rateGroup1Hz.RateGroupMemberOut[4] -> sensorUart.run
      rateGroup1Hz.RateGroupMemberOut[5] -> adc.run
      # bufferManager.schedIn is left unconnected: it reports telemetry, telemetry goes
      # through the hub, and the hub takes its buffers from that same buffer manager.
    }

    connections Hub {
      # Commands from the Cdh computer, and their responses
      hub.cmdDispOut[0]       -> cmdDisp.seqCmdBuff[0]
      cmdDisp.seqCmdStatus[0] -> hub.cmdRespIn[0]

      hub.allocate   -> bufferManager.bufferGetCallee
      hub.deallocate -> bufferManager.bufferSendIn

      # Hub <-> buffer adapter <-> Ethernet driver
      hub.toBufferDriver         -> hubAdapter.bufferIn
      hubAdapter.bufferInReturn  -> hub.toBufferDriverReturn
      hubAdapter.bufferOut       -> hub.fromBufferDriver
      hub.fromBufferDriverReturn -> hubAdapter.bufferOutReturn

      hubAdapter.toByteStreamDriver         -> hubDriver.$send
      hubDriver.$recv                       -> hubAdapter.fromByteStreamDriver
      hubAdapter.fromByteStreamDriverReturn -> hubDriver.recvReturnIn
      hubDriver.ready                       -> hubAdapter.byteStreamDriverReady
      hubDriver.allocate                    -> bufferManager.bufferGetCallee
      hubDriver.deallocate                  -> bufferManager.bufferSendIn
    }

    connections Devices {
      # IMU and power monitor on one I2C bus, at different addresses
      imu.i2cWriteRead   -> i2cBus.writeRead
      power.i2cWriteRead -> i2cBus.writeRead

      # Serial temperature sensor on a UART
      sensorUart.ready           -> sensor.drvConnected
      sensorUart.$recv           -> sensor.drvReceiveIn
      sensor.drvReceiveReturnOut -> sensorUart.recvReturnIn
      sensor.drvSendOut          -> sensorUart.$send
      sensorUart.allocate        -> bufferManager.bufferGetCallee
      sensorUart.deallocate      -> bufferManager.bufferSendIn

      # Heater enable pin and over-temperature switch
      heater.heaterWrite      -> heaterPin.gpioWrite
      heater.switchRead       -> switchPin.gpioRead
      switchPin.gpioInterrupt -> heater.switchTripped

      # Converter on the SPI bus
      adc.spiWriteRead -> spiBus.SpiWriteRead
    }

  }

}
