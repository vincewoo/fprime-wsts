module Payload {

  deployment topology Payload {

    # ----------------------------------------------------------------------
    # Instances
    # ----------------------------------------------------------------------

    instance rateGroup1Hz
    instance cmdDisp
    instance posixTime
    instance rateGroupDriver
    instance instrument
    instance textLogger
    instance hub
    instance hubAdapter
    instance bufferManager
    instance linuxTimer
    instance hubDriver

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

      rateGroupDriver.CycleOut[0] -> rateGroup1Hz.CycleIn
      rateGroup1Hz.RateGroupMemberOut[0] -> instrument.run
      rateGroup1Hz.RateGroupMemberOut[1] -> cmdDisp.run
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

  }

}
