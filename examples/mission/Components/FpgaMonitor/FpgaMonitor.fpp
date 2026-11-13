module Components {

  @ Driver for the payload FPGA's housekeeping block. It reads and writes the block's
  @ registers through a register interface component, and services its frame interrupt.
  active component FpgaMonitor {

    @ Register reads, to the register interface the block is behind
    output port regRead: SensorLib.RegRead32

    @ Register writes
    output port regWrite: SensorLib.RegWrite32

    @ The block's interrupt. Called in interrupt context: it only queues the service.
    sync input port interruptIn: SensorLib.RegInterrupt

    @ Rate group input: sample the block
    async input port run: Svc.Sched

    @ Services an interrupt on this component's own thread
    internal port serviceInterrupt(number: U32)

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut

    @ Write the block's LED register
    async command FPGA_SET_LEDS(pattern: U32)

    event FpgaIdentified($id: U32) \
      severity activity high format "FPGA housekeeping block identified: {x}"

    @ The identity register does not read as this block's
    event FpgaIdMismatch($id: U32) \
      severity warning high format "FPGA identity register reads {x}" \
      throttle 3

    @ A register read as all ones: the block is not answering
    event FpgaReadInvalid(offset: U32) \
      severity warning low format "FPGA register {x} reads as all ones" \
      throttle 3

    event FrameOverrun \
      severity warning low format "FPGA reported a frame overrun" \
      throttle 5

    event LedsSet(pattern: U32) \
      severity activity high format "FPGA LEDs set to {x}"

    @ Board temperature measured by the block, in degrees Celsius
    telemetry Temperature: F32

    @ Frames the block has counted
    telemetry FrameCount: U32

    @ Interrupts serviced
    telemetry Interrupts: U32

    @ Value last written to the block's heartbeat register
    telemetry Heartbeat: U32

  }

}
