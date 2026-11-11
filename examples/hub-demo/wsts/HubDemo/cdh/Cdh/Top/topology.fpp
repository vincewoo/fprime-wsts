module Cdh {

  enum Ports_RateGroups {
    rateGroup10Hz
    rateGroup1Hz
  }

  deployment topology Cdh {

    # WSTS instances (generated)
    instance wstsSetup
    instance wstsControl


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
    instance cmdSplitter
    instance hub
    instance hubAdapter
    instance linuxTimer
    instance comDriver
    instance hubDriver

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
      rateGroup10Hz.RateGroupMemberOut[0] -> ComCcsds.Subtopology.comQueueRun
      rateGroup10Hz.RateGroupMemberOut[1] -> ComCcsds.Subtopology.aggregatorTimeout
      rateGroup10Hz.RateGroupMemberOut[2] -> CdhCore.Subtopology.cmdDispRun
      rateGroup10Hz.RateGroupMemberOut[3] -> CdhCore.Subtopology.eventsRun

      rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup1Hz] -> rateGroup1Hz.CycleIn
      rateGroup1Hz.RateGroupMemberOut[0] -> CdhCore.Subtopology.tlmSendRun
      rateGroup1Hz.RateGroupMemberOut[1] -> CdhCore.Subtopology.healthRun
      rateGroup1Hz.RateGroupMemberOut[2] -> ComCcsds.Subtopology.bufferManagerSchedIn
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

      # Commands from the ground go to this computer's dispatcher or, by opcode, to the hub
      ComCcsds.Subtopology.commandOut     -> cmdSplitter.CmdBuff[0]
      cmdSplitter.LocalCmd[0]             -> CdhCore.Subtopology.seqCmdBuff
      CdhCore.Subtopology.seqCmdStatus    -> cmdSplitter.seqCmdStatus[0]
      cmdSplitter.forwardSeqCmdStatus[0]  -> ComCcsds.Subtopology.cmdResponseIn
    }

    connections Hub {
      # Commands for the payload computer, and their responses
      cmdSplitter.RemoteCmd[0] -> hub.cmdDispIn[0]
      hub.cmdRespOut[0]        -> cmdSplitter.seqCmdStatus[0]

      # Events and telemetry from the payload computer join this computer's own
      hub.eventOut -> CdhCore.events.LogRecv
      hub.tlmOut   -> CdhCore.tlmSend.TlmRecv

      hub.allocate   -> ComCcsds.Subtopology.commsBufferGetCallee
      hub.deallocate -> ComCcsds.Subtopology.commsBufferSendIn

      # Hub <-> buffer adapter <-> Ethernet driver
      hub.toBufferDriver         -> hubAdapter.bufferIn
      hubAdapter.bufferInReturn  -> hub.toBufferDriverReturn
      hubAdapter.bufferOut       -> hub.fromBufferDriver
      hub.fromBufferDriverReturn -> hubAdapter.bufferOutReturn

      hubAdapter.toByteStreamDriver         -> hubDriver.$send
      hubDriver.$recv                       -> hubAdapter.fromByteStreamDriver
      hubAdapter.fromByteStreamDriverReturn -> hubDriver.recvReturnIn
      hubDriver.ready                       -> hubAdapter.byteStreamDriverReady
      hubDriver.allocate                    -> ComCcsds.Subtopology.commsBufferGetCallee
      hubDriver.deallocate                  -> ComCcsds.Subtopology.commsBufferSendIn
    }

  }

}
