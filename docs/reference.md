# Reference

## Command line

| Command | Purpose |
|---|---|
| `fprime-wsts generate <wsts.yaml> [--project DIR] [--output DIR] [--check] [--json]` | Create or refresh a simulated deployment. `--project` defaults to the manifest's `project`, else the nearest `settings.ini` above the manifest. `--check` writes nothing and exits 1 if the output is out of date |
| `fprime-wsts generate <wsts-system.yaml> [--output DIR] [--check] [--json]` | The same for every element of a [system](systems.md), plus `wsts-system.json` |
| `fprime-wsts run [DIR] [--gds] [--scenario F] [--scenario-mode stimulus\|responses] [--record F] [--duration S] [--bridge ADDR] [--files-dir DIR] [--json]` | Run a built simulated deployment, optionally with a headless GDS. Logs go to `DIR/wsts-logs` |
| `fprime-wsts run [SYSTEM DIR] [--gds] [--duration S] [--json]` | Run every element of a generated system, with their links, and optionally one GDS on the ground element with the merged dictionary |
| `fprime-wsts report [DIR] [--json]` | Print a generated deployment's fidelity report |
| `fprime-wsts scaffold <Component> --output DIR [--project DIR] [--name NAME]` | Start a mirror surrogate for a flight component |

Errors the user can fix are printed as `fprime-wsts: error: …` with exit code 2.

## Manifest (`wsts.yaml`)

Only `deployment` is required.

A manifest either lives in the flight project, or names the flight project with `project`
and makes its own directory a standalone **simulation project** (see
[Where simulation code lives](surrogate-guide.md#where-simulation-code-lives)). Paths to
flight things (`deployment`, `sources`, `main.sources`, `cmake.*_subdirectories`,
`libraries`) are always relative to the flight project root. Paths to simulation things
(`catalogs`, `output`) are relative to the simulation project when there is one, else to
the flight project root.

```yaml
project: ../flight               # flight project root, relative to this file (standalone simulation project)
deployment: LinuxDemo            # deployment directory (the one containing Top/)
topology: LinuxDemo.LinuxDemo    # only needed if Top/ defines more than one topology
output: wsts/LinuxDemo            # default: wsts/<deployment> in the simulation project, else in the flight project

keep: [comDriver]                # instances never substituted (see "Instance names")
substitute:                      # explicit choices; override the catalog
  - instance: radioUart          # an instance name ...
    with: Wsts.SimByteStream
    endpoint: radio              # bus endpoint name; default is the instance's base name
    options: "chunk=256"         # passed to the surrogate's wstsBind()
  - component: Proj.FpgaDriver   # ... or every instance of a component
    with: Proj.SimFpga

models:                          # in-process C++ device models
  - name: imu                    # used by model_set / MODEL_SET
    class: Wsts::RegisterMapI2c
    header: Wsts/Core/Models.hpp  # default
    endpoint: imuBus
    kind: i2c                    # bytestream | i2c | spi | gpio | none
    address: 0x68                # i2c only
    params: {"0x75": "0x68"}     # each becomes model.set(key, value) at start-up

control:                         # or `control: none`
  enabled: true
  base_id: 0x10030000            # default: first 0x1000 block above all flight IDs
bridge: tcp:50100                # tcp:<port> | tcp:<host>:<port> | unix:<path> | none
tick_hz: 10                      # only used when no tick source was substituted

catalogs: [wsts-catalog.yaml]    # project surrogate catalogs, searched before the built-in one
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
defines: [MY_SIM_FLAG]               # compile definitions, in addition to WSTS_SIM=1
```

### Instance names

A topology's instances include those of the subtopologies it imports, each under its
qualified FPP name: `SubtopDemo.vxTimer` is defined in the deployment's own module,
`SensorLink.uart` in the `SensorLink` subtopology of a library. Wherever a manifest names
an instance (`keep`, `substitute: - instance:`, the ends of a system's `links`) it may give:

- the qualified name, which always works: `SensorLink.uart`;
- the base name, or any trailing part of the qualified name, when exactly one instance
  ends with it: `uart`. A name that fits several instances is an error that lists them.

Catalog `replaces` entries match by component, so they apply to subtopology instances
without being asked. The report, the dictionary differences and `wstsBind` calls use
qualified names.

A bus endpoint is named after the instance's base name. When two substituted instances
share one (a `uart` in each of two subtopologies), generation stops and asks for an
`endpoint:` on one of them:

```yaml
substitute:
  - {instance: GpsLink.uart, with: Wsts.SimByteStream, endpoint: gpsUart}
```

An instance defined outside the deployment's `Top` directory is retyped in a copy of its
FPP file, and the overlay builds that copy in place of the original module. Nothing in
the manifest asks for this. See
[Instances inside subtopologies](design.md#instances-inside-subtopologies).

There are no manifest keys for configuration overrides either: files replaced with
`register_fprime_config(CONFIGURATION_OVERRIDES …)` are found and applied as the build
applies them. If the analysis picks the wrong file (two configuration modules of one
project overriding the same file, for instance), list the unwanted one under
`fpp_exclude`.

## System manifest (`wsts-system.yaml`)

For a flight system of several computers. See [systems.md](systems.md).

```yaml
system: HubDemo                  # required: a name for the system
ground: cdh                      # element the GDS connects to; default: the first
output: wsts/HubDemo             # default: wsts/<system>, relative to this file
elements:                        # required: element name -> deployment manifest
  cdh:
    manifest: Cdh/wsts.yaml      # relative to this file
    ids: [0x00000000, 0x1FFFFFFF]  # IDs routed to this element; its WSTS instances go inside
    bridge: tcp:50100            # default: the manifest's, else tcp:50100, tcp:50101, ...
  payload: Payload/wsts.yaml     # short form
links:
  - name: hub                    # also the name faults are addressed by
    kind: bytestream             # default
    a: cdh.hubDriver             # <element>.<instance>: a byte stream driver instance;
                                 # qualified names work too (cdh.RadioLink.uart)
    b: payload.hubDriver
```

An instance at the end of a link is substituted even if no catalog entry replaces its
component (with `Wsts.SimByteStream`, chosen by `link`). It cannot be listed under `keep`,
and its endpoint cannot also have a `bytestream` model.

## Surrogate catalog

```yaml
surrogates:
  - surrogate: Wsts.SimTick
    replaces: [Svc.LinuxTimer, VxWorksSvc.VxWatchDogTimer]
    options: "mode=block"
    options_for: {VxWorksSvc.VxWatchDogTimer: "mode=thread tick_hz=60"}
    compat: |      # C++ for topology code: declarations that lived in the driver's header
      ...
```

Built-in surrogates (`catalog/wsts-catalog.yaml`):

| Surrogate | Interface | Replaces | `wstsBind` options |
|---|---|---|---|
| `Wsts.SimByteStream` | `Drv.ByteStreamDriver` + `allocate`, `deallocate`, `run` | `Drv.PosixUartDriver`, `Drv.LinuxUartDriver`, `VxWorksDrv.VxWorksUartDriver`; any byte stream driver at the end of a system link (`Drv.TcpClient`, `Drv.TcpServer`, `Drv.Udp`) | `chunk=<bytes>` largest buffer per `recv` (default 1024) |
| `Wsts.SimI2c` | `Drv.I2c` | `Drv.LinuxI2cDriver` | |
| `Wsts.SimSpi` | `Drv.Spi` | `Drv.LinuxSpiDriver` | |
| `Wsts.SimGpio` | `Drv.Gpio` (one pin per instance) | `Drv.LinuxGpioDriver` | |
| `Wsts.SimTick` | `Drv.Tick` | `Svc.LinuxTimer`, `VxWorksSvc.VxWatchDogTimer` | `mode=block\|thread`, `tick_hz=<n>` |

## Actions

Used identically by the bridge, scenario files and (through its commands) `Wsts.SimControl`.

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

Relative `path` values resolve against the directory in `WSTS_DIR` (`fprime-wsts run` sets it
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
  `tick`, `endpoint`, `src` (`flight`, `model`, `link`, `inject`, `replay`) and the data. They are
  ignored in `stimulus` mode and replayed in `responses` mode.

## Bridge protocol

Newline-delimited JSON. A client sends `{"id": n, "op": …}` and receives
`{"id": n, "ok": true, …}` or `{"id": n, "ok": false, "error": "…"}`. Beyond the actions:

| `op` | Purpose |
|---|---|
| `status` | Tick, run state, fault and scenario state, bus counters, endpoint names, and `links` / `links_up` (link ends of a system element, and how many are connected) |
| `subscribe` | Start receiving `{"ev": "rec", …}`, `{"ev": "action", …}` and `{"ev": "sim_event", …}` |
| `gpio_get` (`endpoint`) | Level flight software last wrote |
| `attach` (`endpoint`, `kind`: `i2c`\|`spi`, `addr`) | Become the device model. The sim then sends `{"ev": "i2c_req", "rid", "endpoint", "addr", "write", "rlen"}` and waits up to 2 s for `{"op": "reply", "rid", "status", "data"}` |

Byte stream and GPIO models need no `attach`: subscribe, watch `bs_tx` / `gpio_out`
records, and answer with `inject_bytes` / `gpio_force`.

`fprime_wsts.bridge.WstsBridge` wraps all of this. `fprime_wsts.bridge.WstsSystem` holds one
`WstsBridge` per element of a system and addresses faults by link name.

## Environment variables read by a simulated deployment

| Variable | Meaning |
|---|---|
| `WSTS_BRIDGE` | Bridge address, overriding the manifest |
| `WSTS_SCENARIO`, `WSTS_SCENARIO_MODE` | Scenario to load and start at start-up |
| `WSTS_RECORD` | File to record to from start-up |
| `WSTS_DIR` | Base directory for relative scenario and recording paths |
| `WSTS_LINK_DIR` | Directory of the link sockets of a system (`<link>.sock`). Set by `fprime-wsts run`; without it links stay down |

## `Wsts.SimControl`

Commands mirror the actions (see the README). Events: `SimPaused`, `SimResumed`,
`SimRateSet`, `FaultArmed`, `FaultTriggered` (first application of a fault only),
`FaultExpired`, `FaultsCleared`, `Injected`, `ScenarioLoaded`, `ScenarioStarted`,
`ScenarioCompleted`, `ScenarioStopped`, `ScenarioError`, `RecordStarted`,
`RecordStopped`, `ActionFailed`, `SimLog`. Telemetry, once a second: `SimTicks`,
`SimRunState`, `SimTimeScale`, `ActiveFaults`, `FaultHits`, `ScenarioRunState`,
`ScenarioStep`, `ScenarioSteps`, `Recording`, `BusTransactions`, `BusBytes`, `BusErrors`.

Command string arguments are limited by the project's `FW_CMD_STRING_MAX_SIZE` (40
characters by default). Name scenario files relative to `WSTS_DIR`, and send long payloads
through the bridge or a scenario file.
