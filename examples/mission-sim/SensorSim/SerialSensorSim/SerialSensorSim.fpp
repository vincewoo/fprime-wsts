module SensorSim {

  @ Simulator of the serial temperature sensor that SensorLib.SerialSensor manages.
  @ An ordinary F Prime component: it talks to a byte stream driver, is scheduled by a
  @ rate group and is commanded and observed through the ground system like any other.
  queued component SerialSensorSim {

    # ----------------------------------------------------------------------
    # The serial line, as a client of a byte stream driver
    # ----------------------------------------------------------------------

    @ The line is up
    sync input port drvConnected: Drv.ByteStreamReady

    @ Bytes from the flight computer. Queued: handled on this component's rate group
    async input port drvReceiveIn: Drv.ByteStreamData

    @ Return of buffers that came in on drvReceiveIn
    output port drvReceiveReturnOut: Fw.BufferSend

    @ Bytes to the flight computer
    output port drvSendOut: Drv.ByteStreamSend

    # ----------------------------------------------------------------------
    # Transmit buffers and scheduling
    # ----------------------------------------------------------------------

    @ Buffer for a report
    output port bufferGetOut: Fw.BufferGet

    @ Return of a buffer obtained on bufferGetOut
    output port bufferReturnOut: Fw.BufferSend

    @ One report per call, and the queued input is handled here
    sync input port schedIn: Svc.Sched

    # ----------------------------------------------------------------------
    # Dictionary
    # ----------------------------------------------------------------------

    @ Temperature the sensor reports, in degrees C
    param TEMPERATURE: F32 default 21.5

    @ Offset added to the reported temperature until changed
    sync command SET_BIAS(bias: F32)

    @ The flight computer sent a query
    event QueryReceived(query: string size 40) \
      severity activity low format "Sensor simulator received query: {}"

    @ A report could not be sent
    event ReportDropped severity warning low format "Sensor simulator has no transmit buffer" \
      throttle 5

    @ Reports sent to the flight computer
    telemetry ReportsSent: U32

    @ Queries received from the flight computer
    telemetry QueriesReceived: U32

    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut
    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    param get port prmGetOut
    param set port prmSetOut

  }

}
