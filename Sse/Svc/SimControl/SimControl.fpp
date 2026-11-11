module Sse {

  @ Kind of fault applied to transactions on an SSE bus endpoint
  enum SimFaultKind : U8 {
    DROP = 0      @< Discard the data or write; I2C and SPI transactions fail
    CORRUPT = 1   @< XOR every byte with the low 8 bits of arg
    DELAY = 2     @< Hold the transaction for arg milliseconds
    DUPLICATE = 3 @< Deliver byte stream data twice
    ERROR = 4     @< Return driver status arg to flight software
    STUCK = 5     @< Reads return arg; writes are ignored
  }

  @ Direction a fault applies to
  enum SimFaultDir : U8 {
    BOTH = 0
    TO_DEVICE = 1 @< Flight software to device
    TO_FLIGHT = 2 @< Device to flight software
  }

  enum SimRunState : U8 {
    RUNNING = 0
    PAUSED = 1
  }

  enum SimScenarioState : U8 {
    IDLE = 0
    LOADED = 1
    RUNNING = 2
    COMPLETED = 3
    FAILED = 4
  }

  @ How a scenario or recording is replayed
  enum SimReplayMode : U8 {
    STIMULUS = 0  @< Re-apply actions against the live device models
    RESPONSES = 1 @< Also replay recorded device responses, bypassing the models
  }

  @ Command, telemetry and event front end of the SSE simulation control core.
  @ Wired into simulated deployments automatically; never part of a flight topology.
  @
  @ String arguments are limited by the project's FW_CMD_STRING_MAX_SIZE (40 characters
  @ by default), whatever size is declared here. File arguments may therefore be given
  @ relative to the directory in the SSE_DIR environment variable, and longer payloads
  @ are better sent through the bridge or a scenario file.
  active component SimControl {

    # ----------------------------------------------------------------------
    # Standard ports
    # ----------------------------------------------------------------------

    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut
    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller

    @ Raised by the simulation tick to publish telemetry and pending events
    internal port tickNotify() drop

    # ----------------------------------------------------------------------
    # Commands: simulation run control
    # ----------------------------------------------------------------------

    @ Stop advancing simulation time; flight rate groups stop cycling
    async command SIM_PAUSE opcode 0x00

    @ Resume advancing simulation time
    async command SIM_RESUME opcode 0x01

    @ Set the simulation time scale relative to the wall clock
    async command SIM_SET_RATE(
      rate: F32 @< 1.0 is real time; 2.0 runs twice as fast
    ) opcode 0x02

    # ----------------------------------------------------------------------
    # Commands: fault injection
    # ----------------------------------------------------------------------

    @ Arm a fault on a bus endpoint
    async command FAULT_SET(
      endpoint: string size 40 @< Bus endpoint name
      kind: SimFaultKind
      direction: SimFaultDir
      arg: U32 @< Mask, milliseconds, status or stuck value, depending on kind
      count: U32 @< Number of transactions to affect; 0 for no limit
      ticks: U32 @< Simulation ticks until the fault expires; 0 for no limit
    ) opcode 0x10

    @ Clear the faults on one endpoint, or on all endpoints with "*"
    async command FAULT_CLEAR(
      endpoint: string size 40
    ) opcode 0x11

    # ----------------------------------------------------------------------
    # Commands: direct input/output injection
    # ----------------------------------------------------------------------

    @ Deliver bytes, given in hex, to flight software as if received from the device
    async command INJECT_BYTES(
      endpoint: string size 40
      data: string size 200 @< Hex digits, e.g. 24475047
    ) opcode 0x20

    @ Deliver a text string to flight software as if received from the device
    async command INJECT_TEXT(
      endpoint: string size 40
      $text: string size 200
    ) opcode 0x21

    @ Drive a GPIO input pin
    async command GPIO_FORCE(
      endpoint: string size 40
      level: Fw.Logic
    ) opcode 0x22

    @ Set a register of a simulated I2C device
    async command I2C_REG_SET(
      endpoint: string size 40
      addr: U32 @< I2C device address
      $reg: U8
      value: U8
    ) opcode 0x23

    @ Set a parameter of a device model
    async command MODEL_SET(
      model: string size 40
      key: string size 40
      value: string size 120
    ) opcode 0x24

    # ----------------------------------------------------------------------
    # Commands: scenarios and recording
    # ----------------------------------------------------------------------

    @ Load a scenario or recording file
    async command SCENARIO_LOAD(
      path: string size 200
      mode: SimReplayMode
    ) opcode 0x30

    @ Start the loaded scenario at the current tick
    async command SCENARIO_START opcode 0x31

    @ Stop the running scenario
    async command SCENARIO_STOP opcode 0x32

    @ Start recording bus traffic and simulation actions to a file
    async command RECORD_START(
      path: string size 200
    ) opcode 0x33

    @ Stop recording
    async command RECORD_STOP opcode 0x34

    # ----------------------------------------------------------------------
    # Events
    # ----------------------------------------------------------------------

    event SimPaused(tick: U32) \
      severity activity high id 0x00 \
      format "Simulation paused at tick {}"

    event SimResumed(tick: U32) \
      severity activity high id 0x01 \
      format "Simulation resumed at tick {}"

    event SimRateSet(rate: string size 20) \
      severity activity high id 0x02 \
      format "Simulation time scale set to {}"

    event FaultArmed(faultId: U32, endpoint: string size 40, kind: string size 20, arg: U32) \
      severity activity high id 0x10 \
      format "Fault {} armed on {}: {} arg {}"

    event FaultTriggered(faultId: U32, endpoint: string size 40, kind: string size 20) \
      severity warning low id 0x11 \
      format "Fault {} on {} first applied: {}"

    event FaultExpired(faultId: U32, endpoint: string size 40, kind: string size 20, hits: U32) \
      severity activity high id 0x12 \
      format "Fault {} on {} ({}) expired after {} applications"

    event FaultsCleared(endpoint: string size 40, count: U32) \
      severity activity high id 0x13 \
      format "Faults cleared on {}: {}"

    event Injected(endpoint: string size 40, what: string size 40, a: U32, b: U32) \
      severity activity low id 0x20 \
      format "Injected on {}: {} ({}, {})"

    event ScenarioLoaded(path: string size 120, steps: U32) \
      severity activity high id 0x30 \
      format "Scenario {} loaded with {} steps"

    event ScenarioStarted(path: string size 120, steps: U32) \
      severity activity high id 0x31 \
      format "Scenario {} started, {} steps"

    event ScenarioCompleted(path: string size 120, steps: U32) \
      severity activity high id 0x32 \
      format "Scenario {} completed after {} steps"

    event ScenarioStopped(path: string size 120, step: U32) \
      severity activity high id 0x33 \
      format "Scenario {} stopped at step {}"

    event ScenarioError(message: string size 120) \
      severity warning high id 0x34 \
      format "Scenario error: {}"

    event RecordStarted(path: string size 120) \
      severity activity high id 0x35 \
      format "Recording to {}"

    event RecordStopped(lines: U32) \
      severity activity high id 0x36 \
      format "Recording stopped after {} lines"

    event ActionFailed(message: string size 120) \
      severity warning high id 0x40 \
      format "Simulation action failed: {}"

    event SimLog(message: string size 120) \
      severity activity low id 0x41 \
      format "SSE: {}"

    # ----------------------------------------------------------------------
    # Telemetry
    # ----------------------------------------------------------------------

    @ Simulation ticks since start
    telemetry SimTicks: U64 id 0x00

    telemetry SimRunState: SimRunState id 0x01

    @ Simulation time scale relative to the wall clock
    telemetry SimTimeScale: F32 id 0x02

    @ Faults currently armed
    telemetry ActiveFaults: U32 id 0x03

    @ Total number of transactions a fault has been applied to
    telemetry FaultHits: U32 id 0x04

    telemetry ScenarioRunState: SimScenarioState id 0x05

    @ Next scenario step to execute
    telemetry ScenarioStep: U32 id 0x06

    @ Steps in the loaded scenario
    telemetry ScenarioSteps: U32 id 0x07

    telemetry Recording: bool id 0x08

    @ Transactions seen on the simulation bus
    telemetry BusTransactions: U32 id 0x09

    @ Bytes moved over the simulation bus
    telemetry BusBytes: U32 id 0x0A

    @ Transactions that returned an error status to flight software
    telemetry BusErrors: U32 id 0x0B

  }

}
