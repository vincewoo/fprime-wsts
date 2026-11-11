module Components {

  @ Device manager for a serial temperature sensor that reports lines of the form
  @ "$TEMP,<degrees C>\n" and accepts free-form query strings.
  passive component SerialSensor {

    import Drv.ByteStreamDriverClient

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut

    @ Send a query string to the sensor
    sync command SEND_QUERY(query: string size 40)

    @ The serial link to the sensor is up
    event SensorLinkReady \
      severity activity high format "Serial sensor link ready"

    @ A line arrived that is not a temperature report
    event UnrecognizedLine(line: string size 60) \
      severity warning low format "Unrecognized sensor line: {}"

    @ A query could not be sent
    event QueryFailed(status: Drv.ByteStreamStatus) \
      severity warning high format "Sensor query failed: {}"

    telemetry Temperature: F32
    telemetry LinesReceived: U32
    telemetry BadLines: U32

  }

}
