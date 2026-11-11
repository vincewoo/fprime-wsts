module SubtopDemo {

  enum Ports_RateGroups {
    rateGroup10Hz
    rateGroup1Hz
  }

  deployment topology SubtopDemo {

    # Framework subtopologies
    instance CdhCore.Subtopology
    instance ComCcsds.Subtopology

    # Library subtopology: the serial sensor and its UART driver
    instance SensorLink.Subtopology

    instance rateGroup10Hz
    instance rateGroup1Hz
    instance rateGroupDriver
    instance chronoTime
    instance comDriver
    instance vxTimer

    command connections instance CdhCore.cmdDisp
    event connections instance CdhCore.events
    telemetry connections instance CdhCore.tlmSend
    text event connections instance CdhCore.textLogger
    health connections instance CdhCore.$health
    time connections instance chronoTime

    connections RateGroups {
      vxTimer.CycleOut -> rateGroupDriver.CycleIn

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup10Hz] -> rateGroup10Hz.CycleIn
      rateGroup10Hz.RateGroupMemberOut[0] -> ComCcsds.Subtopology.comQueueRun
      rateGroup10Hz.RateGroupMemberOut[1] -> ComCcsds.Subtopology.aggregatorTimeout
      rateGroup10Hz.RateGroupMemberOut[2] -> CdhCore.Subtopology.cmdDispRun
      rateGroup10Hz.RateGroupMemberOut[3] -> CdhCore.Subtopology.eventsRun

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup1Hz] -> rateGroup1Hz.CycleIn
      rateGroup1Hz.RateGroupMemberOut[0] -> CdhCore.Subtopology.tlmSendRun
      rateGroup1Hz.RateGroupMemberOut[1] -> CdhCore.Subtopology.healthRun
      rateGroup1Hz.RateGroupMemberOut[2] -> ComCcsds.Subtopology.bufferManagerSchedIn
      rateGroup1Hz.RateGroupMemberOut[3] -> SensorLink.Subtopology.uartRun
      rateGroup1Hz.RateGroupMemberOut[4] -> SensorLink.Subtopology.bufferManagerRun
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

  }

}
