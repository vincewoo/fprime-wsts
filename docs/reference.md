# Reference

## Command line

| Command | Purpose |
|---|---|
| `fprime-sse generate <sse.yaml> [--project DIR] [--output DIR] [--check] [--json]` | Create or refresh a simulated deployment. `--check` writes nothing and exits 1 if the output is out of date |
| `fprime-sse run [DIR] [--gds] [--scenario F] [--scenario-mode stimulus\|responses] [--record F] [--duration S] [--bridge ADDR] [--files-dir DIR] [--json]` | Run a built simulated deployment, optionally with a headless GDS. Logs go to `DIR/sse-logs` |
| `fprime-sse report [DIR] [--json]` | Print a generated deployment's fidelity report |
| `fprime-sse scaffold <Component> --output DIR [--project DIR] [--name NAME]` | Start a mirror surrogate for a flight component |

Errors the user can fix are printed as `fprime-sse: error: …` with exit code 2.

## Manifest (`sse.yaml`)

Paths are relative to the project root. Only `deployment` is required.

```yaml
deployment: LinuxDemo            # deployment directory (the one containing Top/)
topology: LinuxDemo.LinuxDemo    # only needed if Top/ defines more than one topology
output: sse/LinuxDemo            # default: sse/<deployment>

keep: [comDriver]                # instances never substituted
substitute:                      # explicit choices; override the catalog
  - instance: radioUart          # an instance name ...
    with: Sse.SimByteStream
    endpoint: radio              # bus endpoint name; default is the instance name
    options: "chunk=256"         # passed to the surrogate's sseBind()
  - component: Proj.FpgaDriver   # ... or every instance of a component
    with: Proj.SimFpga

models:                          # in-process C++ device models
  - name: imu                    # used by model_set / MODEL_SET
    class: Sse::RegisterMapI2c
    header: Sse/Core/Models.hpp  # default
    endpoint: imuBus
    kind: i2c                    # bytestream | i2c | spi | gpio | none
    address: 0x68                # i2c only
    params: {"0x75": "0x68"}     # each becomes model.set(key, value) at start-up

control:                         # or `control: none`
  enabled: true
  base_id: 0x10030000            # default: first 0x1000 block above all flight IDs
bridge: tcp:50100                # tcp:<port> | tcp:<host>:<port> | unix:<path> | none
tick_hz: 10                      # only used when no tick source was substituted

catalogs: [sim/sse-catalog.yaml] # project surrogate catalogs, searched before the built-in one
sources:
  fpp: [...]                     # topology model files (default: Top/CMakeLists.txt AUTOCODER_INPUTS)
  topology: [...]                # hand-written topology sources (default: Top/CMakeLists.txt SOURCES)
main:
  sources: [...]                 # default: SOURCES of register_fprime_deployment
  generate: true                 # write a Linux main() for a deployment that has none
  declarations: |
    extern "C" int start(const char* hostname, int port);
  start: start(hostname, port)   # hostname and port come from -a and -p
  stop: stop()
cmake:
  extra_subdirectories: [Lib/Math]   # modules with no FPP model that components depend on
  exclude_subdirectories: [...]
  depends: [Proj_Math]               # extra link dependencies of the topology module
libraries: {keep: [...], drop: [...]}
fpp_exclude: ["*/Experimental/*"]    # FPP files to leave out of the analysis
defines: [MY_SIM_FLAG]               # compile definitions, in addition to SSE_SIM=1
```

## Surrogate catalog

```yaml
surrogates:
  - surrogate: Sse.SimTick
    replaces: [Svc.LinuxTimer, VxWorksSvc.VxWatchDogTimer]
    options: "mode=block"
    options_for: {VxWorksSvc.VxWatchDogTimer: "mode=thread tick_hz=60"}
    compat: |      # C++ for topology code: declarations that lived in the driver's header
      ...
```

Built-in surrogates (`catalog/sse-catalog.yaml`):

| Surrogate | Interface | Replaces | `sseBind` options |
|---|---|---|---|
| `Sse.SimByteStream` | `Drv.ByteStreamDriver` + `allocate`, `deallocate`, `run` | `Drv.PosixUartDriver`, `Drv.LinuxUartDriver`, `VxWorksDrv.VxWorksUartDriver` | `chunk=<bytes>` largest buffer per `recv` (default 1024) |
| `Sse.SimI2c` | `Drv.I2c` | `Drv.LinuxI2cDriver` | |
| `Sse.SimSpi` | `Drv.Spi` | `Drv.LinuxSpiDriver` | |
| `Sse.SimGpio` | `Drv.Gpio` (one pin per instance) | `Drv.LinuxGpioDriver` | |
| `Sse.SimTick` | `Drv.Tick` | `Svc.LinuxTimer`, `VxWorksSvc.VxWatchDogTimer` | `mode=block\|thread`, `tick_hz=<n>` |

## Actions

Used identically by the bridge, scenario files and (through its commands) `Sse.SimControl`.

| `op` | Fields | Effect |
|---|---|---|
| `pause`, `resume` | | Stop / restart the simulation tick |
| `set_rate` | `rate` (0.01–1000) | Simulated time per wall-clock time |
| `fault_set` | `endpoint`, `kind`, `dir`=`both`, `arg`=0, `count`=0, `ticks`=0 | Arm a fault; `count`/`ticks` of 0 mean unbounded |
| `fault_clear` | `endpoint`=`*` | Remove faults |
| `inject_bytes` | `endpoint`, `data` (hex) or `text` | Bytes to flight software as if from the device |
| `gpio_force` | `endpoint`, `level` | Drive a GPIO input |
| `i2c_reg_set` | `endpoint`, `addr`, `reg`, `value` | Set a register of a register-addressed I2C model |
| `model_set` | `model`, `key`, `value` | Set a model parameter |
| `record_start` / `record_stop` | `path` | Record bus traffic and actions |
| `scenario_load` | `path`, `mode`=`stimulus` | Load a scenario or recording |
| `scenario_start` / `scenario_stop` | | |
| `log` | `text` | Marker event |

Relative `path` values resolve against the directory in `SSE_DIR` (`fprime-sse run` sets it
to the log directory, or `--files-dir`).

### Faults

`dir` is `both`, `to_device` (flight software → device) or `to_flight`.

| `kind` | Byte stream | I2C / SPI | GPIO | `arg` |
|---|---|---|---|---|
| `drop` | Data discarded | Transaction fails (write or read error) | Write ignored; input change ignored | |
| `corrupt` | Every byte XOR `arg` | Written or read bytes XOR `arg` | | 8-bit mask |
| `delay` | Held for `arg` ms | Held for `arg` ms | Held for `arg` ms | milliseconds |
| `duplicate` | Delivered twice | | | |
| `error` | Send returns an error status | Transaction returns status `arg` | Call returns status `arg` | driver status value (0 means 1) |
| `stuck` | Data discarded | Read bytes all `arg` | Reads return `arg`; writes ignored | value |

For I2C, status values are those of `Drv.I2cStatus` (1 address, 2 write, 3 read, 5 other).

## Scenario and recording files

One JSON object per line. Blank lines and lines starting with `#` are ignored.

- Action lines have `tick` (relative to scenario start) and the action fields above.
- Recording lines have `rec` (`bs_tx`, `bs_rx`, `i2c`, `spi`, `gpio_out`, `gpio_in`),
  `tick`, `endpoint`, `src` (`flight`, `model`, `inject`, `replay`) and the data. They are
  ignored in `stimulus` mode and replayed in `responses` mode.

## Bridge protocol

Newline-delimited JSON. A client sends `{"id": n, "op": …}` and receives
`{"id": n, "ok": true, …}` or `{"id": n, "ok": false, "error": "…"}`. Beyond the actions:

| `op` | Purpose |
|---|---|
| `status` | Tick, run state, fault and scenario state, bus counters, endpoint names |
| `subscribe` | Start receiving `{"ev": "rec", …}`, `{"ev": "action", …}` and `{"ev": "sim_event", …}` |
| `gpio_get` (`endpoint`) | Level flight software last wrote |
| `attach` (`endpoint`, `kind`: `i2c`\|`spi`, `addr`) | Become the device model. The sim then sends `{"ev": "i2c_req", "rid", "endpoint", "addr", "write", "rlen"}` and waits up to 2 s for `{"op": "reply", "rid", "status", "data"}` |

Byte stream and GPIO models need no `attach`: subscribe, watch `bs_tx` / `gpio_out`
records, and answer with `inject_bytes` / `gpio_force`.

`fprime_sse.bridge.SseBridge` wraps all of this.

## Environment variables read by a simulated deployment

| Variable | Meaning |
|---|---|
| `SSE_BRIDGE` | Bridge address, overriding the manifest |
| `SSE_SCENARIO`, `SSE_SCENARIO_MODE` | Scenario to load and start at start-up |
| `SSE_RECORD` | File to record to from start-up |
| `SSE_DIR` | Base directory for relative scenario and recording paths |

## `Sse.SimControl`

Commands mirror the actions (see the README). Events: `SimPaused`, `SimResumed`,
`SimRateSet`, `FaultArmed`, `FaultTriggered` (first application of a fault only),
`FaultExpired`, `FaultsCleared`, `Injected`, `ScenarioLoaded`, `ScenarioStarted`,
`ScenarioCompleted`, `ScenarioStopped`, `ScenarioError`, `RecordStarted`,
`RecordStopped`, `ActionFailed`, `SimLog`. Telemetry, once a second: `SimTicks`,
`SimRunState`, `SimTimeScale`, `ActiveFaults`, `FaultHits`, `ScenarioRunState`,
`ScenarioStep`, `ScenarioSteps`, `Recording`, `BusTransactions`, `BusBytes`, `BusErrors`.

Command string arguments are limited by the project's `FW_CMD_STRING_MAX_SIZE` (40
characters by default). Name scenario files relative to `SSE_DIR`, and send long payloads
through the bridge or a scenario file.
