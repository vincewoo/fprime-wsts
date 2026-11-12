module Components {

  @ Device manager for an SPI analog-to-digital converter (an MCP3008-style part) that
  @ samples the instrument's detector temperature
  passive component Adc {

    @ SPI bus the converter is on
    output port spiWriteRead: Drv.SpiWriteRead

    @ Rate group input: take one conversion
    sync input port run: Svc.Sched

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller

    @ An SPI transaction with the converter failed
    event SampleError(status: Drv.SpiStatus) \
      severity warning low format "ADC conversion failed: {}" \
      throttle 5

    @ Raw conversion of the detector temperature channel, 0 to 1023
    telemetry DetectorCounts: U16

  }

}
