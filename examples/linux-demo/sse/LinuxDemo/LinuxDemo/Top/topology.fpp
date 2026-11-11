module LinuxDemo {

  enum Ports_RateGroups {
    rateGroup10Hz
    rateGroup1Hz
  }

  deployment topology LinuxDemo {

    # SSE instances (generated)
    instance sseSetup
    instance sseControl


    # ----------------------------------------------------------------------
    # Subtopologies
    # ----------------------------------------------------------------------

    instance CdhCore.Subtopology
    instance ComCcsds.Subtopology

    # ----------------------------------------------------------------------
    # Instances
    # ----------------------------------------------------------------------

    instance rateGroup10Hz
    instance rateGroup1Hz
    instance rateGroupDriver
    instance posixTime
    instance imu
    instance sensor
    instance heater
    instance linuxTimer
    instance comDriver
    instance imuBus
    instance sensorUart
    instance heaterPin
    instance switchPin

    # ----------------------------------------------------------------------
    # Pattern graph specifiers
    # ----------------------------------------------------------------------

    command connections instance CdhCore.cmdDisp
    event connections instance CdhCore.events
    telemetry connections instance CdhCore.tlmSend
    text event connections instance CdhCore.textLogger
    health connections instance CdhCore.$health
    time connections instance posixTime

    # ----------------------------------------------------------------------
    # Direct graph specifiers
    # ----------------------------------------------------------------------

    connections RateGroups {
      linuxTimer.CycleOut -> rateGroupDriver.CycleIn

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup10Hz] -> rateGroup10Hz.CycleIn
      rateGroup10Hz.RateGroupMemberOut[0] -> imu.run
      rateGroup10Hz.RateGroupMemberOut[1] -> ComCcsds.Subtopology.comQueueRun
      rateGroup10Hz.RateGroupMemberOut[2] -> ComCcsds.Subtopology.aggregatorTimeout
      rateGroup10Hz.RateGroupMemberOut[3] -> CdhCore.Subtopology.cmdDispRun
      rateGroup10Hz.RateGroupMemberOut[4] -> CdhCore.Subtopology.eventsRun

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup1Hz] -> rateGroup1Hz.CycleIn
      rateGroup1Hz.RateGroupMemberOut[0] -> CdhCore.Subtopology.tlmSendRun
      rateGroup1Hz.RateGroupMemberOut[1] -> CdhCore.Subtopology.healthRun
      rateGroup1Hz.RateGroupMemberOut[2] -> ComCcsds.Subtopology.bufferManagerSchedIn
      rateGroup1Hz.RateGroupMemberOut[3] -> heater.run
      rateGroup1Hz.RateGroupMemberOut[4] -> sensorUart.run
    }

    connections Communications {
      comDriver.allocate   -> ComCcsds.Subtopology.commsBufferGetCallee
      comDriver.deallocate -> ComCcsds.Subtopology.commsBufferSendIn

      comDriver.$recv                          -> ComCcsds.Subtopology.drvReceiveIn
      ComCcsds.Subtopology.drvReceiveReturnOut -> comDriver.recvReturnIn

      ComCcsds.Subtopology.drvSendOut -> comDriver.$send
      comDriver.ready                 -> ComCcsds.Subtopology.drvConnected
    }

    connections ComCcsds_CdhCore {
      CdhCore.Subtopology.eventsPktSend  -> ComCcsds.Subtopology.comPacketQueueIn[ComCcsds.Ports_ComPacketQueue.EVENTS]
      CdhCore.Subtopology.tlmSendPktSend -> ComCcsds.Subtopology.comPacketQueueIn[ComCcsds.Ports_ComPacketQueue.TELEMETRY]

      ComCcsds.Subtopology.commandOut  -> CdhCore.Subtopology.seqCmdBuff
      CdhCore.Subtopology.seqCmdStatus -> ComCcsds.Subtopology.cmdResponseIn
    }

    connections Devices {
      # IMU on the I2C bus
      imu.i2cWriteRead -> imuBus.writeRead

      # Serial temperature sensor on a UART
      sensorUart.ready             -> sensor.drvConnected
      sensorUart.$recv             -> sensor.drvReceiveIn
      sensor.drvReceiveReturnOut   -> sensorUart.recvReturnIn
      sensor.drvSendOut            -> sensorUart.$send
      sensorUart.allocate          -> ComCcsds.Subtopology.commsBufferGetCallee
      sensorUart.deallocate        -> ComCcsds.Subtopology.commsBufferSendIn

      # Heater enable pin and over-temperature switch
      heater.heaterWrite       -> heaterPin.gpioWrite
      heater.switchRead        -> switchPin.gpioRead
      switchPin.gpioInterrupt  -> heater.switchTripped
    }

  }

}
