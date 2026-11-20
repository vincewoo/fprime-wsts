# Reference

## Command line

| Command | Purpose |
|---|---|
| `fprime-wsts survey [DIR]... [--deployment NAME]... [--no-trial] [--json]` | For every F´ project under the directories (default: the current one), list each deployment's hardware instances and what the generator would do with each, byte stream drivers by role (ground link, hub link, device), register interface components, tick and time sources, entry points, ID range, and the result of a trial generation with a minimal manifest. Writes nothing. Exit status 1 when no project is found |
| `fprime-wsts init <flight project> [--output DIR] [--deployment NAME]... [--no-trial] [--no-submodule] [--models] [--json]` | The same survey of one flight project, then a simulation project written from it: `wsts.yaml` for one deployment, or `<element>.yaml` for each and `wsts-system.yaml` for several, and a `.gitignore`. Default directory: `<project>-sim` beside the flight project. Decisions it cannot make are marked `TODO`. Never overwrites a file, and refuses a directory inside the flight project. A new project (no manifest in the directory yet) also gets the running toolkit as a git submodule at `lib/fprime-wsts`, at this checkout's commit, with its `origin` as the URL; the directory becomes a git repository if it is in none. Nothing is committed. `--no-submodule` leaves it out. In `--json`, `toolkit` says what was done. `--models` also starts a device model for each bus endpoint the survey finds, as `new-model` does (`Models/<Endpoint>Model.hpp`, `.cpp`, the module and `library.cmake`), named in the manifests with `cmake.depends: [Models]`; an I2C one at `address: any`. A hub link end gets none: the other element is its device |
| `fprime-wsts generate <wsts.yaml> [--project DIR] [--output DIR] [--check] [--json]` | Create or refresh a simulated deployment. `--project` defaults to the manifest's `project`, else the nearest `settings.ini` above the manifest. `--check` writes nothing and exits 1 if the output is out of date |
| `fprime-wsts generate <wsts-system.yaml> [--output DIR] [--check] [--json]` | The same for every element of a [system](systems.md), plus `wsts-system.json` |
| `fprime-wsts build [DIR or manifest] [-j N] [--element NAME]... [--fresh] [-D NAME=VALUE]... [--keep-going] [--project DIR] [--json]` | Configure and build a generated deployment, or every element of a generated system; given a manifest, `generate` first. The configure step (`fprime-util generate`, with the report's `configure_options` and the `-D` options) runs when there is no build directory, when one was left unfinished, with `--fresh`, and when what a build directory caches has changed since this command configured it (the overlay's cache entries, its `settings.ini`, the options; recorded in `wsts-build.json` in the build directory). A build directory configured by hand is used as it is. Output goes to `DIR/wsts-logs/build.log` (`wsts-logs/<element>/build.log` for a system). Exit status 1 when a build fails; it stops at the first element that fails unless `--keep-going` |
| `fprime-wsts run [DIR] [--gds] [--gds-gui] [--gds-port P] [--gds-gui-port P] [--gds-gui-addr A] [--scenario F] [--scenario-mode stimulus\|responses] [--record F] [--duration S] [--bridge ADDR] [--auto-ports] [--stepped \| --realtime] [--files-dir DIR] [--env NAME=VALUE]... [--json]` | Run a built simulated deployment, optionally with the GDS. `--stepped` runs in [stepped time](stepped-time.md) (experimental): ticks are taken as fast as flight software finishes them, and `--duration` counts simulated seconds. A system runs in [lockstep](stepped-time.md#phase-4-what-is-built): its elements are stepped together on one time base, and a plain `--scenario F` is a [system scenario](#system-scenarios). `--realtime` overrides a manifest that says `time: {mode: stepped}`. `--auto-ports` takes free ports for the bridge, the GDS link and the GDS GUI in place of the fixed ones, and gives the GDS sockets of its own, so several runs fit on one machine. `--gds` is headless; `--gds-gui` starts the browser GUI (implies `--gds`) and prints its URL. Logs go to `DIR/wsts-logs`. `--env` sets an environment variable of the deployment, over the manifest's `run.env` |
| `fprime-wsts run [SYSTEM DIR] [--gds] [--gds-gui] [--auto-ports] [--duration S] [--scenario ELEMENT=F]... [--scenario-mode M] [--record [ELEMENT=]F]... [--record-merged F] [--scenario-merged F] [--env [ELEMENT.]NAME=VALUE]... [--json]` | Run every element of a generated system, with their links, and optionally one GDS on the ground element with the merged dictionary. `--scenario payload=F` plays a scenario on one element; `--record payload=F` records one element, and a plain `--record F` every element, each to `<stem>.<element>.<suffix>`. `--record-merged F` records every element to one file, one line per recording line, each with `"element": <name>`: the lines are per element in the system's element order, each element's in the order it recorded them, not re-sorted (ticks are relative to each element's own recording start). Each element records to a temp file under the log directory while the run is up; they are merged when it stops, and removed once the merge succeeds (a failed merge names the element and leaves them). `--scenario-merged F` is its inverse: the file is split by `element` and each element plays its own part from start-up, in `--scenario-mode` (`stimulus` or `responses`). `--record-merged` cannot be given with `--record`, nor `--scenario-merged` with `--scenario`; both are errors on a single deployment, which has `--record`/`--scenario` for its one file. `--env NAME=VALUE` goes to every element, `--env payload.NAME=VALUE` to one |
| `fprime-wsts status [DIR] [--element NAME] [--json]` | The state of the simulation running from a generated directory, read through its bridge: tick, run state, rate, links, bus counters, faults, scenario, endpoints. For a system, every element or the one named. Exit status 2 when nothing answers |
| `fprime-wsts ctl DIR [--element NAME] OP [FIELD=VALUE]...` | Send one [action](#actions) (or a bridge query such as `gpio_get`) to a running simulation and print the answer as JSON: `fprime-wsts ctl DIR fault_set endpoint=i2cBus kind=error arg=3 count=5`. A value is a JSON value where it reads as one (`3`, `true`), else text, in which `\n`, `\t` and `\xNN` are those characters (`'text=$TEMP,48.0\n'`). In a system, `pause`, `resume`, `set_rate`, `fault_clear` and `status` go to every element unless `--element` names one; any other action needs `--element` |
| `fprime-wsts check-host <DIR or manifest> [--project DIR] [--json]` | List what the host must provide for the simulated build (system headers, libraries, packages, SDK files, sanitizer runtime) and whether it does, with the host and flight compilers. Compiles nothing of the project. Exit status 1 when something is missing; a check that cannot be made is a warning. Given a generated directory it also writes `wsts-host.md` and `wsts-host.json` there |
| `fprime-wsts report [DIR] [--json]` | Print a generated deployment's fidelity report, and the host check of this machine if `generate` or `check-host` left one |
| `fprime-wsts scaffold <Component> --output DIR [--project DIR] [--name NAME]` | Start a mirror surrogate for a flight component |
| `fprime-wsts new-model <Name> --kind i2c\|spi\|bytestream\|gpio\|register [--endpoint E] [--address A\|any] [--python] [--project DIR] [--manifest FILE] [--namespace NS] [--json]` | Start a [device model](user-guide/device-models.md#start-one-with-new-model) that builds and answers from the first run, with TODOs where the device's behaviour goes. C++: `Models/<Name>.hpp` and `.cpp` in the simulation project (default: the current directory), added to `Models/CMakeLists.txt` (made, or added to when `new-model` made it), and `library.cmake` when there is none. `--python`: `pymodels/<name>.py`, which defines `serve(sim)`. The model is named in the manifest (`--manifest`, else the project's one deployment manifest) under `models` with `cmake.depends: [Models]`, or under `run.models`, edited as text so its comments stay; what cannot be placed is printed to add by hand. Its name there is the class name with a lowercase first letter. An I2C model is at `address: any` unless `--address` says. Never overwrites a file |

Errors the user can fix are printed as `fprime-wsts: error: …` with exit code 2.

A command given a path in a simulation project that pins the toolkit (a `lib/fprime-wsts`
in the directory of the path or one above it) is run by that toolkit when it is not the
one running: `generate`, `check-host`, `build`, `report`, `run`, `status`, `ctl`,
`scaffold` (by `--output`) and `new-model` (by `--project`). It runs in the same Python environment with the pinned
`python/` first on the path, and says so on stderr. A `lib/fprime-wsts` that is not checked
out is an error ([Simulation project](user-guide/simulation-project.md#the-toolkit-submodule)).

`fprime-wsts build --json` prints `{output, ok, elements: {<name>: {directory, configure, reason,
stage, exit_code, seconds, log, binary}}}` and sends the build output to the log only.
`configure` is `configure`, `reconfigure` or `keep`, with the `reason`; `stage` is the step
reached (`configure` or `build`). An element that failed has `errors` in place of `binary`:
the compiler, linker and CMake error lines of its log, at most 20.

`fprime-wsts run --json` prints one summary when the run ends: `deployment`, `exit_code`,
`exited_early`, `interrupted`, `seconds`, `logs`, `bridge`, `gds_port`, `gds_gui`, `final_status`
(for a system `bridge` is per element). While a run is up, `wsts-run.json` in its log
directory holds the addresses it uses (`bridge` or `elements: {<name>: <bridge>}`,
`gds_port`, `gds_gui`, `zmq_transport`, `pid`); `fprime-wsts status`, `fprime-wsts ctl`,
`WstsSystem(<directory>)` and `fprime_wsts.bridge.bridge_address(<directory>)` read it, so
they find a run started with `--auto-ports`. It is removed when the run stops. A run in
stepped time adds `stepped`, `sim_seconds` (simulated time reached), `speed` (simulated
over wall time) and, when a tick did not finish, `stuck` (the queues that held work; the
exit status is then 1). When the
deployment exits on its own and its log ends with an F´ assert, the summary has
`assert: {file, line, instances}` (and `id` when the log printed a file ID, `0x…:line`,
which is resolved with `hashes.txt` of the build cache). `instances` are the instances of
the component the file belongs to, each with `kept: true` if the simulation did not
substitute it. The same is printed on standard error, with the `substitute` entry to add
when a kept instance is the cause. A deployment that stops before it opens its bridge is
reported the same way, with `error` in the summary and exit code 2.

## Manifest (`wsts.yaml`)

Only `deployment` is required. An unknown key is an error, which names the key that was
probably meant (`substitutes`: did you mean `substitute`?) or the section it belongs under
(`stub_variants` goes under `cmake:`). `schemas/wsts.schema.json` and
`schemas/wsts-system.schema.json` describe the two manifest files for editors.

A manifest either lives in the flight project, or names the flight project with `project`
and makes its own directory a standalone **simulation project** (see
[Where simulation code lives](surrogate-guide.md#where-simulation-code-lives)). Paths to
flight things (`deployment`, `sources`, `main.sources`, `cmake.*_subdirectories`,
`libraries`) are always relative to the flight project root. Paths to simulation things
(`catalogs`, `output`) are relative to the simulation project when there is one, else to
the flight project root.

```yaml
project: ../flight               # flight project root, relative to this file (standalone simulation project)
deployment: Payload              # deployment directory (the one containing Top/)
topology: Payload.Payload        # only needed if Top/ defines more than one topology
topology_dir: Payload            # the directory of the topology, relative to the deployment, when it is not Top/ (default: Top/,
                                 # else the one directory the deployment's CMakeLists.txt adds that defines a topology)
output: wsts/Payload              # default: wsts/<deployment> in the simulation project, else in the flight project

keep: [comDriver]                # instances never substituted (see "Instance names")
gpio_endpoints: [ppsOut]         # GPIO endpoints with no surrogate: a model or `setup` code drives them through the
                                 # bus, and a gpio link of a system may end at one (see "Links" in systems.md)
substitute:                      # explicit choices; override the catalog
  - instance: radioUart          # an instance name ...
    with: Wsts.SimByteStream
    endpoint: radio              # bus endpoint name; default is the instance's base name.
                                 # `endpoint: none`: the surrogate does not use the bus
    options: "chunk=256"         # passed to the surrogate's wstsBind()
    register_endpoint: true      # the surrogate reads registers through ports of its own shape: its endpoint
                                 # takes a `kind: register` model (see "Register blocks")
    bytestream_endpoint: true    # the surrogate serves a byte stream through ports of its own shape (a polled
                                 # UART): a link of a system may end at it (see "Links" in systems.md)
    gpio_endpoint: true          # the surrogate serves a GPIO pin through ports of its own shape (a pulse-capture
                                 # driver): a gpio link of a system may end at it (see "Links" in systems.md)
    ports: {runIn: schedIn, regOut: }   # flight port -> surrogate port; empty = the surrogate has none
    queue_size: 10               # for a queued or active surrogate replacing an instance without one
    stack_size: 64 * 1024        # active surrogates only, as are priority and cpu
    priority: 30
  - component: Proj.FpgaDriver   # ... or every instance of a component
    with: Proj.SimFpga

models:                          # in-process C++ device models
  - name: imu                    # used by model_set / MODEL_SET
    class: Wsts::RegisterMapI2c
    header: Wsts/Core/Models.hpp  # default
    endpoint: i2cBus
    kind: i2c                    # bytestream | i2c | spi | gpio | register | none
    address: 0x68                # i2c only; `any`: every address no other device on the endpoint has
    params: {"0x75": "0x68"}     # each becomes model.set(key, value) at start-up
    telemetry: true              # default: a channel per register of a stock register model (below)
  - name: fpga                   # a block of memory-mapped registers (see "Register blocks")
    class: Wsts::RegisterFile
    endpoint: fpgaRegs           # the endpoint of a substituted register interface instance
    kind: register               # one block per endpoint; offsets and widths are the model's
    params: {"reg.0x00": "0xF00D0001", "irq.period": "20"}

simulators:                      # F´ instances the simulation adds (see "Simulator components")
  topologies: [SensorSim.Simulator]      # subtopologies, by qualified name
  instances: [SensorSim.powerModel]      # single instances, by qualified name
  headers: [SensorSim/Top/SubtopologyTopologyDefs.hpp]   # included for their phase code
  connections:                           # between added instances and flight ones
    - rateGroup1Hz.RateGroupMemberOut -> SensorSim.Simulator.schedIn
devices:                         # adapter instances that are the device on a bus endpoint
  - instance: SensorSim.sensorPort       # an instance of the adapter for that endpoint's kind:
    endpoint: uart                       # BusByteStreamDevice, BusI2cDevice, BusSpiDevice or
                                         # BusGpioDevice
    options: "chunk=256"         # passed to wstsServe(): chunk=<bytes> on a byte stream
                                 # endpoint, addr=<n> (required) on an I2C endpoint

clocks:                          # rates of the simulation's own cycle sources
  - instance: SensorSim.simClock         # an instance of Wsts.SimCycle, added by `simulators`
    hz: 125                              # cycles per second of simulated time, up to 100000

ids: [0x00000000, 0x0FFFFFFF]    # the IDs that reach this deployment, as flight software routes them (a
                                 # Svc.CmdSplitter sends opcodes from one ID up to another computer): simulator
                                 # instances outside it are an error. Optional; a system's `ids` for the
                                 # element wins. The WSTS instances go inside it too

control:                         # or `control: none`
  enabled: true
  base_id: 0x10030000            # default: first 0x1000 block above all flight IDs
bridge: tcp:50100                # tcp:<port> | tcp:<host>:<port> | unix:<path> | none
tick_hz: 10                      # only used when no tick source was substituted
time:
  mode: realtime                 # or stepped: ticks are taken when flight software has finished the last one
                                 # (experimental; `fprime-wsts run --stepped` / `--realtime` choose per run)
  epoch: 2030-01-01T00:00:00Z    # where simulated time starts in stepped time: a date and time (UTC unless it
                                 # says otherwise) or seconds since 1970. Default 2000-01-01T00:00:00Z

catalogs: [wsts-catalog.yaml]    # project surrogate catalogs, searched before the built-in one
sources:
  fpp: [...]                     # topology model files (default: the AUTOCODER_INPUTS of the topology directory's
                                 # CMakeLists.txt, Top/ or `topology_dir`; inputs of other autocoders are handed on)
  topology: [...]                # hand-written topology sources (default: SOURCES of that CMakeLists.txt)
main:
  sources: [...]                 # default: SOURCES of register_fprime_deployment
  generate: true                 # write a Linux main() for a deployment that has none
  declarations: |
    extern "C" int start(const char* hostname, int port);
  start: start(hostname, port)   # hostname and port come from -a and -p
  stop: stop()
setup:                           # C++ run by the generated set-up once the surrogates are bound and the
  headers: [Proj/Dma/Dma.hpp]    # models attached, before the runtime starts. Or just `setup: |` with the text
  code: |
    Wsts::Dma::addRegion(Proj::Dma::hostBase(), Proj::Dma::physicalBase(), Proj::Dma::size());
cmake:
  extra_subdirectories: [Lib/Math]   # modules with no FPP model that components depend on
  exclude_subdirectories: [...]      # modules to leave out, wherever they are added (project, library, framework)
  build_on_host: [...]               # modules that restrict_platforms() keeps off a Linux host, built anyway
  depends: [Proj_Math]               # extra link dependencies of the topology module
  cache:                             # CMake cache variables, set before F´ reads the libraries
    SIM_COMPONENTS: [SerialSensorSim, Top]   # a list becomes a CMake list
    SIM_LEVEL: 2
  include: [sim.cmake]               # CMake included after all modules, before the deployment
  include_early: [options.cmake]     # CMake included before the framework and libraries are read
  stub_variants: [Components/Imager] # modules read with BUILD_TESTING set: their stub variant (see "Bridging the host")
  cxx_flags: ["-include cstring"]    # flags of the host C++ compiler; or {<module directory>: [flags]}
  bits: 32                           # 32-bit executable (x86 -m32); default 64
libraries:
  keep: [...]                        # flight libraries to build although nothing in the model uses them
  drop: [...]
  sim: [lib/sim-common]              # further libraries of the simulation (relative to the simulation project)
run:
  env:                               # environment of the simulated deployment when it is run
    SIM_PROFILE: ./profiles/calm.txt # ./ or ../ : a path relative to this file
    SIM_SEED: 42
  models: [models/thermo.py]         # Python device models started with the run (each defines serve(sim))
  gds_args: [--framing-selection, fprime]   # added to the fprime-gds command line of a run with --gds
fpp_exclude: ["*/Experimental/*"]    # FPP files to leave out of the analysis
defines: [MY_SIM_FLAG]               # compile definitions, in addition to WSTS_SIM=1
host:
  ignore: [vendor/gen.h, -lcodec]    # names the host check does not report (header, -l<library>, package, SDK entry)
shims: [vxworks]                     # operating system APIs the toolkit implements for project code (see "Shims")
files:
  sandbox: ./wsts-files              # where the files of flight software land on the host (see "Files");
                                     # default: the files directory of the run
```

### Instance names

A topology's instances include those of the subtopologies it imports, each under its
qualified FPP name: `Cdh.vxTimer` is defined in the deployment's own module,
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

### Register blocks

A **register interface component** is what flight device drivers read and write
memory-mapped registers through, and what delivers the hardware's interrupts to them. It
is a component of the project or of one of its libraries, so no catalog lists it: the
manifest names it, and puts a model of the register block on its endpoint.

```yaml
substitute:
  - component: Board.RegisterInterface   # every instance; or `instance: fpgaRegs` for one
    with: Wsts.SimRegister
models:
  - {name: fpga, class: Wsts::RegisterFile, endpoint: fpgaRegs, kind: register,
     params: {"reg.0x00": "0xF00D0001", "irq.period": "20", "irq.status": "0x04"}}
```

Each instance is one endpoint, named after the instance's base name (`endpoint:` gives
another name, and is needed when two substituted instances share a base name). An
endpoint has one register block. Offsets and widths are not in the manifest: they are the
model's.

**A surrogate for register interface components.** `Wsts.SimRegister` has five ports:
`read32In`, `write32In`, `read64In`, `write64In` (input) and `interrupt` (output), with
port types of the toolkit. A flight component has port types of its own, and FPP connects
a port only to one of the same type. So unless the flight component's connected ports
already are those of `Wsts.SimRegister`, the generator writes the surrogate again for it:
a component `WstsAdapted.<Module>_<Component>` in the overlay
(`WstsAdapted/<Module>_<Component>/`), with the flight component's port names and port
types. No `ports:` mapping is needed, whatever the ports are called. The ports are
recognised by their signatures:

| Role | Port |
|---|---|
| read32, read64 | input port, one unsigned integer argument (the offset), returning `U32` or `U64` |
| write32, write64 | input port, two unsigned integer arguments (offset, then a `U32` or `U64` value), returning nothing |
| interrupt | output port with one unsigned integer argument (the number) or none, returning nothing, of a port type that is not the framework's |

A component may have several ports of one role, and need not have all five. Every output
port with the interrupt's shape is called for each interrupt. Command, event, telemetry
and time ports are left out: the flight component's commands and events leave the
dictionary, and the report lists them. A connected port of any other shape is an error
that names it: drop the connection with `ports: {<port>: }` if the simulation can do
without it, or write a mirror surrogate
([A register block](surrogate-guide.md#a-register-block)), which is also how the flight
component's commands and events are kept. Arguments of types other than the built-in
unsigned integers (a project's alias for the offset, say) are not recognised.

The surrogate accepts `configure(...)` and `open(...)` with any arguments, which is what
flight topology code usually calls to open the device (`open` returns `true`). Its
`wstsBind` options are `irq=serial` (the default: one interrupt at a time), `irq=direct`
and `irq=off`.

Behaviour on the bus:

- **A read that fails returns all ones.** The read ports have no status. A fault, an
  offset the model does not have, an endpoint without a model, a bridge model that does not
  answer within two seconds, an exhausted recording: flight software reads `0xFFFFFFFF`
  (or 64 bits of ones), and the access counts in `BusErrors`. No event is raised for the
  read itself; a fault's first application raises `FaultTriggered` as on any endpoint.
- **A write that fails is lost.** Flight software cannot tell.
- **64-bit accesses** reach the model as one access of width 8. `Wsts::RegisterFile`
  serves them as two 32-bit registers, the low half at the offset; a model without 64-bit
  accesses returns an error, and the read is all ones.
- **Interrupts** are raised by the model (`Wsts::Bus::registerInterrupt`, or
  `RegisterFile::raise`), by `irq_raise`, or by a recording. The flight handler runs on the
  thread that raised it: for a model, the simulation's tick thread. That is never the
  flight thread that reads and writes the registers, as an interrupt context is not.
  Deliveries are serialised, so a handler is not re-entered. A handler that posts a
  message to its component's queue, which is what a handler in interrupt context has to
  do, needs nothing more.

`Wsts::RegisterFile` (in `Wsts/Core/Models.hpp`) is the stock model: 32-bit registers at
offsets that are multiples of 4. Parameters, in the manifest and through `model_set`:

| Parameter | Meaning |
|---|---|
| `reg.<offset>` | Value of a register. The first use defines the register, with this as its reset value |
| `reset.<offset>` | Reset value |
| `mask.<offset>` | Write mask: the bits flight software can write (default all) |
| `w1c.<offset>` | `1`: writing a 1 clears the bit instead of setting it |
| `rc.<offset>` | `1`: a flight read returns the value and clears the register |
| `strict` | `1` (default): an access to an offset that is not defined fails. `0`: undefined offsets are plain memory |
| `reset` | `1`: every register back to its reset value |
| `irq.period`, `irq.number` | Raise interrupt `irq.number` every `irq.period` ticks (0: never) |
| `irq.status`, `irq.bits` | Before raising it, set `irq.bits` (default 1) in the register at `irq.status` |

In C++ a model derived from it adds `define()`, a hook per register that runs after a
flight read or write (`onRead`, `onWrite`), `setBits()` and `raise()`.

The report has a "Register blocks" section: each replaced instance, its endpoint and
model, which instances read and write through it, and where its interrupt goes.

**A project surrogate with ports of its own shape.** A driver that is not a register
interface can still read its values from registers: an analog-to-digital driver whose
port takes a channel and returns a conversion, not a register value, for instance. Its
surrogate reads the endpoint with `Wsts::Bus::get().registerRead()` itself, and says so with
`register_endpoint: true` on its `substitute` entry, or on its catalog entry:

```yaml
substitute:
  - {instance: adc, with: Proj.SimAdc, register_endpoint: true}
models:
  - {name: adcBlock, class: Wsts::RegisterFile, endpoint: adc, kind: register,
     params: {"reg.0x14": "2034"}}
```

The generator then attaches the model to the endpoint as it does for a register interface
component. It cannot check the register map: what an offset means is the surrogate's and the
model's. There are no ports to report as register accesses and no interrupt (the report lists
every input port of the surrogate that the topology connects as an access). `register_endpoint`
with `endpoint: none` is an error: the block is attached to an endpoint.

### Set-up code

`setup` adds C++ to the generated `WstsGenerated::setup()`, in a block of its own, after
the surrogates are bound and the models attached and before the runtime (bridge,
recorder, scenario, tick) starts. `setup.headers` are included in that file. It is for
what the manifest cannot express: registering a DMA address translation, or calling a
setter of a flight library that only its unit tests call. The topology's instances are
constructed by then and not yet started. Keep it to a few lines that call functions of
the simulation project.

### Simulator components

`simulators` adds F´ instances to the simulated topology. They are defined in FPP that
the generator can find: in a standalone simulation project, or anywhere in the flight
project or its libraries. See
[Device simulators as F´ components](surrogate-guide.md#device-simulators-as-f-components).

| Key | Meaning |
|---|---|
| `topologies` | Subtopologies to add whole, by qualified name (`<module>.<topology>`). Every instance in them is added, with their internal connections |
| `instances` | Single instances to add, by qualified name |
| `headers` | Headers the phase code of those instances needs. They are included through `WstsCompat.hpp`, because the flight `<Deployment>TopologyDefs.hpp` cannot know them |
| `connections` | Connections with at least one end on an added instance, as `<source> -> <target>` |

An end of a connection is `<instance>.<port>` or `<subtopology>.<exported port>`, where
the instance or subtopology is a flight one or an added one, named as in
[Instance names](#instance-names). A port number in brackets is optional. Without one:

- an output port array (a rate group's `RateGroupMemberOut`) gets the lowest number that
  nothing uses, written into the patched topology. Flight connections keep their numbers.
  When all are taken, generation stops and names the instance, its size and the constant
  that sets it (`ActiveRateGroupOutputPorts`);
- a single output port must be unconnected: an output drives one input.

For a substituted flight instance the surrogate's port names are used. FPP's `$` escapes
may be written or left out (`sensorPort.recv` or `sensorPort.$recv`).

What the generator checks, and what it does to keep flight as it is:

- **IDs.** An added instance keeps the base ID in its definition. It must not overlap a
  flight instance, another added instance or the WSTS instances. The WSTS instances are
  placed above every ID in use, added instances included, unless `control.base_id` says
  otherwise. When the manifest gives `ids: [first, last]` (the IDs that reach this
  deployment), every added instance must lie inside it, and so must the WSTS instances. The
  error names the instances outside the window and the highest flight ID. Flight software
  may route by ID: a `Svc.CmdSplitter` sends the opcodes from one ID up to another
  computer, so a command to an instance above that line is never answered.
- **Health.** Ping ports are numbered by instance name over all instances the health
  pattern reaches, and flight code indexes its ping table by that number. When an added
  instance has ping ports, the patched topology lists the flight targets explicitly
  (`health connections instance $health { ... }`), so flight numbers stay and added
  instances are not pinged.
- **Commands, events, telemetry, time.** The topology's patterns connect added instances
  like any other. The number of instances with commands is checked against the command
  dispatcher's ports, and the opcode total against `CMD_DISPATCHER_DISPATCH_TABLE_SIZE`;
  the parameter total is reported against `PRMDB_NUM_DB_ENTRIES`.
- **Parameters.** If the flight topology has `param connections`, added instances use
  the flight parameter database. If it has none, an added instance with parameters must be
  connected to a `Svc.PrmDb` in the simulation's own FPP.

`devices` names adapter instances, one per kind of bus endpoint. `Wsts.BusByteStreamDevice`
offers `Drv.ByteStreamDriver` (with `allocate` and `deallocate`) to a simulator and is the
device on a byte stream endpoint. `Wsts.BusI2cDevice`, `Wsts.BusSpiDevice` and
`Wsts.BusGpioDevice` are the same for I2C, SPI and GPIO endpoints: they offer the driver
interface their simulator plays the device behind, and answer the bus's transactions
synchronously from it. The endpoint must be bound by a surrogate of the same kind, and for
byte stream, SPI and GPIO cannot also have a model, another device or a system link; on an
I2C endpoint each address has one device, and a model at another address coexists.
`wstsServe` options: `chunk=<bytes>`, the largest buffer per `recv` (default 1024), on a
byte stream endpoint; `addr=<n>`, required, the address the device answers, on an I2C
endpoint.

### Clocks

`clocks` gives each `Wsts.SimCycle` instance its rate. The instance must be one that
`simulators` adds, be of that component, and have its `CycleOut` connected; a
`Wsts.SimCycle` that `clocks` does not list is an error too. `hz` may be fractional, down
to 0.001. The generated set-up calls `wstsClock(hz)` on the instance. Behaviour (simulated
time, pause, spacing, threading) is described under
[A rate of its own](surrogate-guide.md#device-simulators-as-f-components).

### Surrogates without a bus endpoint

`endpoint: none` on a `substitute` entry, or on a catalog entry, says the surrogate talks
to the flight components over F´ ports only. No `wstsBind` call is generated, the instance
is not an endpoint for `models`, `devices` or system links (naming it there is an error),
and the report says "no bus endpoint". An `endpoint: <name>` on a `substitute` entry
overrides a catalog's `none`.

### Libraries, CMake and configuration of the simulation

A standalone simulation project is an F´ library (its `library.cmake` builds its
modules). `libraries.sim` lists further libraries the simulation uses: ones vendored
inside the simulation project, or outside it. Each must have a `library.cmake`. Each is a
root of its own: of the FPP analysis, of module names and header paths, and in the
overlay's `library_locations`, before the simulation project itself. A library that lies
inside another root is still its own root, so its modules have the names they have
wherever else the library is used (`SimCommon_Types`, not
`lib_sim-common_SimCommon_Types`). The simulation project's own `settings.ini`, if it has
one, is not read.

`cmake.include` and `cmake.include_early` name CMake files, relative to the simulation
project, that the overlay's root `CMakeLists.txt` includes:

| Key | Where | What exists there | Use it for |
|---|---|---|---|
| `include_early` | After `find_package(FPrime)`, before the framework's and the libraries' modules are added | The F´ CMake API and the cache; no module targets | Variables and functions a library's `library.cmake` expects |
| `include` | After every framework, library, flight and simulation module is added (and after the simulation's configuration overrides), before the deployment | Every module target except the deployment's | `add_library(<Alias> ALIAS <target>)`, `target_link_libraries` edges between existing modules, `target_include_directories` |

A module may name an alias in `DEPENDS` although the alias is only defined in `include`:
CMake resolves link names when it generates, after all of it has been read.

**Configuration overrides.** A module of the simulation that registers
`CONFIGURATION_OVERRIDES` replaces configuration for the simulated build only:

```cmake
# <simulation project>/config/CMakeLists.txt, added by its library.cmake
register_fprime_config(
    CONFIGURATION_OVERRIDES
        "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp"
    INTERFACE
)
```

F´ lets the last registration of a file name win. The overlay holds such modules back
while the simulation's library is read and adds them after the flight project's modules,
so the simulation's file wins over the framework's, a library's and the flight project's,
including a file that only the flight project supplies. The analysis uses the same order.
This is how a simulation raises `ActiveRateGroupOutputPorts`,
`CmdDispatcherComponentCommandPorts`, `CMD_DISPATCHER_DISPATCH_TABLE_SIZE` or
`PRMDB_NUM_DB_ENTRIES` for the instances it adds. Requirements: the call is in a
`CMakeLists.txt` of its own directory (not in `library.cmake` itself), and each file
replaces one that some earlier module supplies; both are checked. The report lists these
overrides apart from flight's.

### What the simulated build takes from the flight build

Design and reasons: [design.md](design.md#what-is-taken-from-the-flight-build).

- **Libraries.** A flight library is built if the patched model uses a file of it, if it is
  under `libraries.keep`, or if it supplies a configuration file that a module of the build
  overrides with `CONFIGURATION_OVERRIDES`. `libraries.drop` on a library of the last kind is
  an error. The report has `libraries.kept`, `libraries.dropped` and, for libraries kept for
  configuration alone, `libraries.kept_for_configuration: [{library, supplies, file,
  overridden_by}]`.
- **Headers of replaced drivers.** `wsts-include/` holds a stand-in for each header of a
  replaced component that no instance uses any more (`driver_headers` in the report).
- **Modules that add other modules** are added once, by their parent (`nested_modules` in
  the report: `[{module, added_by, in}]`).
- **The flight root `CMakeLists.txt`.** Plain `set()` and `option()` before F´ is read
  become cache entries, and plain compile definitions are copied. Everything else that is
  not F´ boilerplate is a warning with the manifest key to use. `cmake.cache` and `defines`
  win over what is carried. The report has
  `flight_cmake: {file, carried_over: [{line, statement, as}], not_carried_over: [{line,
  statement, why, key}]}`.
- **`cmake.exclude_subdirectories`** leaves a module out wherever it is added. One that the
  overlay would add itself is simply not added; one that a library, the framework or another
  module adds is skipped by the overlay's `add_fprime_subdirectory()` (`excluded_modules` in
  the report). Something else must then provide whatever depended on it: a module of the
  simulation, given the original's target name with `add_library(<name> ALIAS <target>)` in a
  file named under `cmake.include`.
- **`cmake.build_on_host`** lists module directories (relative to the flight project) whose
  `CMakeLists.txt` calls `restrict_platforms()` for a platform other than Linux, and that
  the simulated build adds anyway. Flight's own build of such a module is a unit-test
  build or a cross build, so they are often flight logic that a hardware-driven test left
  off the host: a device manager whose test harness needs the target's types. The overlay
  defines `restrict_platforms()` ahead of F´'s, which passes the call through for every
  module but these, and the planner no longer asks for a surrogate for an instance of one.
  Whatever the module needs from the target's toolchain has to exist on the host: this
  does not supply it, and `check-host` and the build say what is missing. Errors: a path
  that is no module directory, the deployment's own directories, and one that
  `cmake.exclude_subdirectories` leaves out. Report: `built_on_host`.
- **Link dependencies that are files of the target's SDK** are reported as warnings, with
  the ways out: take the module's stub variant if it has one, substitute the instances that
  need the module, drop the library, exclude the module, or point the variable at a host
  build with `cmake.cache`. A module is read in the variant the build takes.

### Bridging the host

Recipes and reasons:
[Building on a host that lacks the target's toolchain](vxworks-conversion.md#building-on-a-host-that-lacks-the-targets-toolchain).

- **`cmake.stub_variants`** lists module directories, relative to the flight project
  (`../` allowed, so a module of any library can be named). Each is read with
  `BUILD_TESTING` set, for that `CMakeLists.txt` alone: no unit-test target, no sanitizer or
  coverage flags, no `BUILD_UT`. Errors: a path that is no module directory, the
  deployment's own directories, a module that `cmake.exclude_subdirectories` leaves out, a
  module of a library that is not built, and a module that the build does not add. A module
  with nothing under `if (BUILD_TESTING)` is a warning. Report: `stub_variants`, and
  `testing_variants: [{module, selected, instances, with_build_testing, without,
  registers_unit_tests, variant_with_build_testing_lacks: {depends, include_directories,
  functions}}]` for every module of the build that has such a branch, listed or not.
- **`cmake.cxx_flags`** is a list of flags for every C++ source of the build, or a mapping
  from a module directory to its flags (`"*"` for every module). One entry is one flag
  with its argument. Report: `cxx_flags`.
- **`cmake.bits`** builds the simulated deployment as a 32-bit executable (x86 `-m32`).
  The generated root `CMakeLists.txt` appends `-m32` to `CMAKE_C_FLAGS`,
  `CMAKE_CXX_FLAGS` and `CMAKE_EXE_LINKER_FLAGS` after `project()`, not through
  `cmake.cache`: `project()` has cached those variables already. Absent or 64, none of the
  lines are written. `check-host` compiles a trivial program with the host compiler,
  `-m32` and `cmake.cxx_flags`; when that fails it lists a missing 32-bit runtime
  (`gcc-multilib`/`g++-multilib` on Debian/Ubuntu). Report: `host_requirements.bits`, only
  when it is 32.
- **`BUILD_TESTING` for the whole build** (`cmake.cache`, the flight root `CMakeLists.txt`,
  the flight `default_cmake_options`) is a warning that says what it turns on.
- **`host.ignore`** names what `check-host` should not report.
- **The host check** reads `host_requirements` of the report, which `generate` writes from
  text alone, so that it is the same on every machine: `modules`, `system_headers:
  {<header>: [{module, file, line, condition?}]}`, `libraries`, `packages: {<name>:
  {required, components, needed_by}}`, `sdk_paths`, `search` (include and link directories
  from the simulation's CMake files, `cxx_flags`), `sanitizers`, `bits` (when the manifest
  asks for 32), `flight_toolchain`,
  `flight_compiler`, `ignore`. What a machine answers is in `wsts-host.json` beside the
  report (`compiler`, `flight_compiler`, `missing: [{kind, name, needed_by, remedies,
  hint?}]`, `notes`, `warnings`, `ok`); that file is not compared by `--check` and is not
  meant to be checked in.

### Shims

`shims` lists operating system APIs that project code calls and the toolkit implements on
the host. The one there is, `vxworks`, is the [VxWorks API shim](vxworks-shim.md)
(`Wsts/VxWorks`). For each, the overlay puts the shim's headers on every module's include
path (after the module's own), links the shim into the deployment as a whole archive, and
the host check counts its headers as provided. The calls it implements in simulated time
are left out of the report's "Code that waits on the host". Report: `shims`, and a section
"Operating system shims". Without it, a module that includes one of its headers without a
guard is a warning that names the key, and `check-host` gives the key as the first remedy.

### Files

Every simulated deployment maps the file system of flight software into one **sandbox**
directory on the host: `Os::File`, `Os::FileSystem` and `Os::Directory` of the deployment
(the overlay chooses `Wsts_Os_File` for all three) hand what flight software names — a
target path such as `/ram0/logs/events.log` or `/tffs0/data.bin`, or a relative name such
as `PrmDb.dat` — to the host under that directory, with the same path under it. What the
target had as mounts is a directory tree the sandbox makes when a write needs it;
climbing above the root of the flight file system stays at its root. A path cannot name
anything outside the sandbox, and the raw I/O API of the VxWorks shim (`open()`,
`read()`, `write()` of `ioLib`) does not go through it.

Relative names resolve against the working directory of flight software, which starts at
the root of the sandbox: what `Os::FileSystem::getWorkingDirectory` reports, and what
`changeWorkingDirectory` changes.

`files.sandbox` is where the sandbox is. `./` and `../` are relative to the manifest's
directory; anything else absolute is used as it is. The default is the files directory of
the run (`WSTS_DIR`, the log directory unless `run --files-dir` says otherwise), so that
the files of flight software land where they always did. A sandbox of its own —
`./wsts-files`, say — survives the clearing of the log directory between runs. Report:
`files`, and a line under "Simulation build and run inputs".

### Surrogates of another kind, or with other port names

A `substitute` entry, or a catalog entry, may say how a surrogate differs in shape:

- `ports` maps flight port names to the surrogate's. Port types and directions must still
  match. An empty value says the surrogate has no such port: flight connections on it are
  commented out in the patched model and listed as fidelity notes. Renames and removals
  are applied wherever the connection is written: in the deployment's topology, in a
  subtopology, or in the `port x = instance.port` line that exports it.
- `queue_size`, `stack_size`, `priority`, `cpu` are written into the instance definition
  when the surrogate is queued or active. A queued or active surrogate that replaces an
  instance without a queue gets `queue size 10` and a warning if none is given.
  Attributes the surrogate's kind does not take are removed.

In a catalog, `ports` applies whatever is replaced and `ports_for: {<component>: {...}}`
to one replaced component.

### CMake cache variables

`cmake.cache` entries are written as `set(<NAME> "<value>" CACHE STRING ...)` in the
overlay's root `CMakeLists.txt`, before `find_package(FPrime)`, so a library that reads
them in its `library.cmake` sees them. A list is written as a CMake list. Precedence,
highest first: `-D` on the command line (`fprime-util generate -DNAME=...`), then
`cmake.cache`, then the flight project's `default_cmake_options`, from which a variable
that `cmake.cache` sets is left out.

`project()` has already run where the entries are written, and it puts some variables in
the cache itself: `CMAKE_<LANG>_FLAGS…`, `CMAKE_<LANG>_COMPILER`, the linker flags,
`CMAKE_BUILD_TYPE`, `CMAKE_INSTALL_PREFIX`. An entry for one of those changes nothing, and
the generator warns. Compiler flags go under `cmake.cxx_flags`. The others are passed when
configuring, as `fprime-util generate -DNAME=VALUE` (directly: after `--`, and for a value
with a space in it, `fprime-util` takes the text for the name of a toolchain); the report
lists them as `configure_options`, and `fprime-wsts build` passes them.

## System manifest (`wsts-system.yaml`)

For a flight system of several computers. See [systems.md](systems.md).

```yaml
system: Mission                  # required: a name for the system
ground: cdh                      # element the GDS connects to; default: the first
output: wsts/Mission             # default: wsts/<system>, relative to this file
elements:                        # required: element name -> deployment manifest
  cdh:
    manifest: cdh.yaml           # relative to this file
    ids: [0x00000000, 0x1FFFFFFF]  # IDs routed to this element; its WSTS instances go inside
    bridge: tcp:50100            # default: the manifest's, else tcp:50100, tcp:50101, ...
  payload: payload.yaml         # short form
links:
  - name: hub                    # also the name faults are addressed by
    kind: bytestream             # default; datagram: connectionless, one message per datagram;
                                 # gpio: a level from the pin flight software at `a` writes to an input at `b`
    a: cdh.hubDriver             # <element>.<instance>: a byte stream driver instance;
                                 # qualified names work too (cdh.RadioLink.uart)
    b: payload.hubDriver
```

An instance at the end of a link is substituted even if no catalog entry replaces its
component (with `Wsts.SimByteStream`, chosen by `link`). It cannot be listed under `keep`,
and its endpoint cannot also have a `bytestream` model.

A `gpio` link carries a pin level, one direction only (`a` drives `b`; the reverse is a second
link with the ends swapped): a `Wsts::LinkGpio` model, its ends replaced by `Wsts.SimGpio`
unless a surrogate says `gpio_endpoint: true`, and an end may instead be a pin listed under
`gpio_endpoints` of its element's manifest ([Links](systems.md#links)).

A `datagram` link carries the same traffic over connectionless Unix datagram sockets: a
`Wsts::LinkDatagram` model instead of `Wsts::LinkByteStream`, one `sendto()` per message
(length, `Control::elapsedNs()`, bytes), each end bound at
`<WSTS_LINK_DIR>/<link>.a.sock` (`a`) and `<link>.b.sock` (`b`) — `address` and
`address_b` set the two paths. It has no connection: a send with no peer's socket is
dropped in real time and held in an outbox in lockstep. Faults, recording and replay apply
as on a stream link; it counts in `links` but never in `links_up`.

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

A catalog entry may also carry `ports`, `ports_for`, `queue_size`, `stack_size`, `priority`
and `cpu` (see [Surrogates of another kind](#surrogates-of-another-kind-or-with-other-port-names)).

Built-in surrogates (`catalog/wsts-catalog.yaml`):

| Surrogate | Interface | Replaces | `wstsBind` options |
|---|---|---|---|
| `Wsts.SimByteStream` | `Drv.ByteStreamDriver` + `allocate`, `deallocate`, `run` | `Drv.PosixUartDriver`, `Drv.LinuxUartDriver`, `VxWorksDrv.VxWorksUartDriver`; any byte stream driver at the end of a system link (`Drv.TcpClient`, `Drv.TcpServer`, `Drv.Udp`) | `chunk=<bytes>` largest buffer per `recv` (default 1024) |
| `Wsts.SimI2c` | `Drv.I2c` | `Drv.LinuxI2cDriver` | |
| `Wsts.SimSpi` | `Drv.Spi` | `Drv.LinuxSpiDriver` | |
| `Wsts.SimGpio` | `Drv.Gpio` (one pin per instance) | `Drv.LinuxGpioDriver` | |
| `Wsts.SimRegister` | 32-bit and 64-bit register reads and writes, an interrupt output ([Register blocks](#register-blocks)) | nothing by itself: the manifest names a project's register interface component | `irq=serial\|direct\|off` |
| `Wsts.SimTick` | `Drv.Tick` | `Svc.LinuxTimer`, `VxWorksSvc.VxWatchDogTimer` | `mode=block\|thread`, `tick_hz=<n>` |
| `Wsts.SimTime` | `Svc.Time` (the time port; no bus endpoint) | `Svc.PosixTime`, `Svc.ChronoTime`, `Svc.OsTime` | |

Stock components that are not surrogates, for instances the simulation adds:
`Wsts.BusByteStreamDevice`, `Wsts.BusI2cDevice`, `Wsts.BusSpiDevice` and
`Wsts.BusGpioDevice` (the device on a byte stream, I2C, SPI or GPIO endpoint, see
`devices`) and `Wsts.SimCycle` (a cycle source with its own rate, see `clocks`).

## Actions

Used identically by the bridge, scenario files and (through its commands) `Wsts.SimControl`.

| `op` | Fields | Effect |
|---|---|---|
| `pause`, `resume` | | Stop / restart the simulation tick |
| `step` | `ticks`=1 | Run until the tick count is `ticks` higher, then pause (`SimPaused`). Exact from a paused simulation and from a scenario; `pause` or `resume` ends a step under way. `status` has `stepping`, the ticks still to go |
| `set_stepped` | `stepped`=true, `lockstep` | Stepped time: a tick is taken when flight software has finished the last one, not when the wall clock says so; what a bridge client sends is applied between two ticks. `false` returns to real time. `status` has `stepped`, `sim_seconds`, `tick_period_ns` and, after a tick that did not finish, `stuck`. `lockstep: true` (an element of a system stepped as a whole): a tick is taken only when the system's conductor grants one (`advance`), and what links bring is held until the first tick after the simulated time it was sent at; `step` is then refused. `lockstep: false` delivers what the links hold |
| `set_rate` | `rate` (0.01–1000) | Simulated time per wall-clock time. In stepped time a limit, and 0 (the default there) is none |
| `fault_set` | `endpoint`, `kind`, `dir`=`both`, `arg`=0, `count`=0, `ticks`=0 | Arm a fault; `count`/`ticks` of 0 mean unbounded |
| `fault_clear` | `endpoint`=`*` | Remove faults |
| `inject_bytes` | `endpoint`, `data` (hex) or `text` | Bytes to flight software as if from the device |
| `gpio_force` | `endpoint`, `level` | Drive a GPIO input |
| `i2c_reg_set` | `endpoint`, `addr`, `reg`, `value` | Set a register of a register-addressed I2C model |
| `reg_set` | `endpoint`, `offset`, `value` | Set a register of the register block on an endpoint, without a flight write (no write mask, no hook). 64-bit values may be given as `"0x…"` strings |
| `irq_raise` | `endpoint`, `number`=0 | Raise an interrupt of the register block on an endpoint |
| `model_set` | `model`, `key`, `value` | Set a model parameter |
| `record_start` / `record_stop` | `path` | Record bus traffic and actions |
| `scenario_load` | `path`, `mode`=`stimulus` | Load a scenario or recording |
| `scenario_start` / `scenario_stop` | | |
| `log` | `text` | Marker event |

Relative `path` values resolve against the directory in `WSTS_DIR` (`fprime-wsts run` sets it
to the log directory, or `--files-dir`).

### Faults

`dir` is `both`, `to_device` (flight software → device) or `to_flight`.

| `kind` | Byte stream | I2C / SPI | GPIO | Register | `arg` |
|---|---|---|---|---|---|
| `drop` | Data discarded | Transaction fails (write or read error) | Write ignored; input change ignored | A write is lost; a read fails (all ones) | |
| `corrupt` | Every byte XOR `arg` | Written or read bytes XOR `arg` | | The value written or read XOR `arg` | 8-bit mask; for a register all 32 bits |
| `delay` | Held for `arg` ms | Held for `arg` ms | Held for `arg` ms | Held for `arg` ms | milliseconds |
| `duplicate` | Delivered twice | | | | |
| `error` | Send returns an error status | Transaction returns status `arg` | Call returns status `arg` | The model is not reached; a read is all ones | driver status value (0 means 1) |
| `stuck` | Data discarded | Read bytes all `arg` | Reads return `arg`; writes ignored | Reads return `arg`; writes ignored | value |

For I2C, status values are those of `Drv.I2cStatus` (1 address, 2 write, 3 read, 5 other).

On a register endpoint a write travels `to_device`, and a read `to_flight`: a fault in one
direction leaves the other alone. Register ports have no status, so the status of a failed
access (1 nothing there, 2 write, 3 read, 4 width, 5 other; or `arg` of an `error` fault)
is in the recording and not in flight software, which reads all ones.

The interrupts of a register endpoint are a fault target of their own, named
`<endpoint>:irq`. Faults on the endpoint itself do not touch them.

| `kind` on `<endpoint>:irq` | Effect |
|---|---|
| `drop` | The interrupt is lost |
| `duplicate` | It is delivered twice |
| `delay` | It is held for `arg` ms, on the thread that raised it |
| `corrupt` | The interrupt number XOR `arg` |

## Scenario and recording files

One JSON object per line. Blank lines and lines starting with `#` are ignored.

- Action lines have `tick` (relative to scenario start) and the action fields above.
- Recording lines have `rec` (`bs_tx`, `bs_rx`, `i2c`, `spi`, `gpio_out`, `gpio_in`,
  `reg_rd`, `reg_wr`, `reg_irq`),
  `tick`, `endpoint`, `src` (`flight`, `model`, `link`, `inject`, `replay`) and the data. They are
  ignored in `stimulus` mode and replayed in `responses` mode. Recorded I2C and SPI
  responses are served in the order flight software asks for them, separately for each
  endpoint and, on I2C, each device address: devices that share a bus keep their own
  responses even when the replay interleaves their transactions differently.
- Register lines:

  ```json
  {"rec": "reg_rd", "tick": 4, "endpoint": "fpgaRegs", "src": "flight", "offset": "0xc", "width": 4, "read": "d7000000", "status": 0}
  {"rec": "reg_wr", "tick": 4, "endpoint": "fpgaRegs", "src": "flight", "offset": "0x10", "width": 4, "write": "2a000000", "status": 0}
  {"rec": "reg_irq", "tick": 20, "endpoint": "fpgaRegs", "src": "model", "number": 1}
  ```

  `offset` is a `"0x…"` string, because an offset may use all 64 bits. `read` and `write`
  are the value as little-endian bytes, `width` of them. In `responses` mode recorded reads
  are served separately for each endpoint and offset, in the order they were recorded for
  that offset; when those are used up, a read of the offset fails (status 5, all ones).
  Writes are recorded and reach no model, and interrupts come from the recording: those a
  model raises during the replay are suppressed.

A **merged recording** (`fprime-wsts run <system> --record-merged F`) is the same lines,
each with `"element": <name>` as its first field: every element's recording in one file,
the elements in the order of the system's elements, and within one element in the order it
recorded them. Ticks stay relative to each element's own recording start, so the lines are
not in tick order across elements and are not re-sorted. `--scenario-merged F` (or
`WSTS_SCENARIO` per element, split by the launcher) turns it back into one file per element,
with the key removed, and plays each on its element from start-up.

## Bridge protocol

Newline-delimited JSON. A client sends `{"id": n, "op": …}` and receives
`{"id": n, "ok": true, …}` or `{"id": n, "ok": false, "error": "…"}`. Beyond the actions:

| `op` | Purpose |
|---|---|
| `status` | Tick, run state, fault and scenario state, bus counters, endpoint names, and `links` / `links_up` (link ends of a system element, and how many have a connected peer: a datagram link counts in `links` and never in `links_up`). Also what flight software has been given and not finished: `counting` (the deployment's queue counts it), `activity` (messages waiting for a task or being handled, and work of the simulation's own threads, now), `work` (the total since start-up) and `busy_ticks` (ticks taken while work of an earlier one was outstanding). For stepped time: `lockstep`, `settled` (start-up has settled), and for each link `link.<name>.sent` and `link.<name>.received`, the messages its end sent (or holds for a peer not yet connected, in lockstep) and received (`WstsBridge.status()` gathers them into `link_traffic`) |
| `advance` (`ticks`=1, `expect.<link>`, `timeout_ms`=30000) | Lockstep only: the conductor of a system grants ticks and waits until they are taken and finished. `expect.<link>`: the messages that link will have received before the tick (what the other end had sent); the tick waits for them, and if they do not come within `WSTS_STEP_LIMIT_MS` it does not finish (`stuck` names the link). Answers the status, with `done` |
| `wait_quiescent` (`timeout_ms`=1000) | Wait until `activity` is zero: flight software has finished everything it was given. Answers `quiescent`, `activity` and, when it is not, `holding`: the queues with work outstanding, as `<queue>: <messages>, …`. Meaningful while paused |
| `subscribe` | Start receiving `{"ev": "rec", …}`, `{"ev": "action", …}` and `{"ev": "sim_event", …}` |
| `gpio_get` (`endpoint`) | Level flight software last wrote |
| `attach` (`endpoint`, `kind`: `i2c`\|`spi`, `addr`) | Become the device model. The sim then sends `{"ev": "i2c_req", "rid", "endpoint", "addr", "write", "rlen"}` and waits up to 2 s for `{"op": "reply", "rid", "status", "data"}` |
| `attach` (`endpoint`, `kind`: `register`) | Become the register block. The sim sends `{"ev": "reg_req", "rid", "endpoint", "access": "read", "offset": "0x…", "width", "rlen"}` or `{…, "access": "write", "offset", "width", "value": "0x…", "rlen": 0}` and waits up to 2 s for `{"op": "reply", "rid", "status", "data"}`, where `data` is the value read as little-endian hex (`width` bytes; empty for a write). Raise the block's interrupts with `irq_raise`. When the client disconnects, the block that was on the endpoint before answers again |

Byte stream and GPIO models need no `attach`: subscribe, watch `bs_tx` / `gpio_out`
records, and answer with `inject_bytes` / `gpio_force`.

`run.models` in the manifest names Python files that define `serve(sim)`; `fprime-wsts run`
starts them in one process (`python -m fprime_wsts.models --bridge <address> <file>...`, log
in `wsts-logs/models.log`) against a deployment started paused, and resumes it when every
`serve` has returned. The report lists them under `run.models`.

`WstsBridge.step()` returns when the ticks have been taken and flight software is quiescent
again; `wait_quiescent()` waits for that by itself and raises with the queues that hold
work up.

`fprime_wsts.bridge.WstsBridge` wraps all of this (`step`, `wait_quiescent`, `serve_i2c`, `serve_spi`,
`serve_bytestream`, `serve_gpio`,
`serve_register`, `raise_irq`, `reg_set`, `register_accesses`, and `submit` to wait on several
simulations at once). `fprime_wsts.bridge.WstsSystem` holds one `WstsBridge` per element of a
system and addresses faults by link name. In lockstep it steps the system as a whole:
`set_stepped()`, `step(seconds=…)` or `step(instants=…)`, `time()`, `run_scenario(path)`,
through `fprime_wsts.lockstep.Conductor`, which has bridge connections of its own (`advance`
blocks its connection until the ticks are finished).

### System scenarios

In lockstep a scenario can be written for the whole system, on one time base
(`fprime-wsts run <system> --stepped --scenario F`, `WstsSystem.run_scenario(F)`). Each line
is an action with `time`, in simulated seconds from the start of the scenario, and the
`element` it is for, or for `fault_set` and `fault_clear` the `link` (faulted at its `a` end,
`direction` as for `WstsSystem.fault_set`):

```json
{"time": 1.0, "element": "payload", "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\n"}
{"time": 2.0, "link": "hub", "op": "fault_set", "kind": "drop", "direction": "b_to_a"}
{"time": 3.0, "link": "hub", "op": "fault_clear"}
```

An action is applied between two instants, once the system's time has reached its own: the
injection above after the instant at 1.0 s, before Payload's tick 21 and Cdh's tick 11. Each
element records it at its own tick. Lines at one time keep their order; blank lines and
lines starting with `#` are skipped.

## Environment variables read by a simulated deployment

| Variable | Meaning |
|---|---|
| `WSTS_BRIDGE` | Bridge address, overriding the manifest |
| `WSTS_SCENARIO`, `WSTS_SCENARIO_MODE` | Scenario to load and start at start-up |
| `WSTS_RECORD` | File to record to from start-up |
| `WSTS_DIR` | Base directory for relative scenario and recording paths; also the sandbox of the files of flight software when `WSTS_FILES_DIR` is not set |
| `WSTS_FILES_DIR` | The sandbox of the files of flight software (see "Files"), over `WSTS_DIR`. Set by `fprime-wsts run` from the manifest's `files.sandbox` |
| `WSTS_STEPPED` | `1`: stepped time; `0`: real time, whatever the manifest's `time.mode`. Set by `fprime-wsts run --stepped` / `--realtime` |
| `WSTS_LOCKSTEP` | `1`: an element of a system stepped as a whole (implies stepped time): its ticks are granted by the conductor. Set by `fprime-wsts run <system> --stepped` |
| `WSTS_EPOCH` | Where simulated time starts, seconds since 1970, over the manifest's `time.epoch` |
| `WSTS_STEP_LIMIT_MS` | Stepped time: wall-clock milliseconds a tick may take to finish before it is reported and the simulation pauses (default 10000) |
| `WSTS_START_PAUSED` | `1`: start paused. `fprime-wsts run` sets it for a deployment with `run.models`, and resumes once they are attached |
| any in `run.env` or `--env` | Set by `fprime-wsts run` and the pytest plugin for the simulator components' phase code |
| `WSTS_LINK_DIR` | Directory of the link sockets of a system. A stream link is `<link>.sock`; a datagram link binds `<link>.a.sock` at its `a` end and `<link>.b.sock` at its `b` end. Set by `fprime-wsts run`; without it links stay down |

### Signals

The registers of `Wsts::RegisterMapI2c` and `Wsts::RegisterFile` (and the `value` of a model
`new-model` starts) take a signal of simulated time in place of a number: stored in the
register on every tick, until a number is given (in the manifest, with `model_set`,
`i2c_reg_set`, `reg_set`). `Wsts/Core/Signal.hpp` and `python/fprime_wsts/signals.py` are the
same language, and give the same values.

| Function (arguments by position or by name, all optional) | Value at `t` seconds |
|---|---|
| `sine(offset=0, amplitude=1, period=1, phase=0)` | `offset + amplitude·sin(2π(t/period + phase))` |
| `square(low=0, high=1, period=1, duty=0.5, phase=0)` | `high` for the first `duty` of each period, else `low` |
| `triangle(low=0, high=1, period=1, phase=0)` | `low` → `high` → `low` over each period |
| `sawtooth(low=0, high=1, period=1, phase=0)` | `low` → `high` over each period |
| `ramp(from=0, to=1, start=0, duration=1)` | `from` until `start`, linearly to `to` over `duration`, then `to` |
| `step(before=0, after=1, at=0)` | `before` until `at`, then `after` |
| `noise(sd=1, mean=0, seed=0)` | Gaussian; a hash of the seed and the instant, so the same in every run |
| `uniform(low=0, high=1, seed=0)` | Uniform in [low, high); the same |

`t` is simulated time in seconds (tick count times tick period: the same in two runs in
stepped time); `+ - * /` and parentheses combine terms; arguments do not depend on `t`.
`<expression> as <format>` says how the value is stored: `u8`, `s8`, `u16`, `s16`, `u24`,
`s24`, `u32` or `s32`, rounded half away from zero and held at the ends of the range. For
`RegisterMapI2c` the value fills as many registers from the one named, with `be` or `le`
(`u16be`) when wider than a byte; the default is `u8`. For `RegisterFile` it is the low bits
of the 32-bit word, no byte order; the default is `u32`. `generate` checks the signals of these
two models; a model of the project checks its own parameters.

```yaml
params:
  "0x02": "sine(offset=14000, amplitude=500, period=30) / 1.25 as u16be"   # RegisterMapI2c
  "reg.0x0C": "215 + noise(sd=2) as s16"                                    # RegisterFile
```

### Register telemetry

Each register a manifest gives `Wsts::RegisterMapI2c` (`"<register>"`) or `Wsts::RegisterFile`
(`reg.<offset>`) a value is a telemetry channel of `wstsModelTlm`, with the register's current
value: `<model>_0x<register>` (`power_0x02`, `fpga_0x000c`). A value of one to four bytes, or a
signal stored in as many, is one channel, of the width (`U8`, `U16`, `U32`) and sign (`I…`) of the
signal's format, big-endian in the order given otherwise; a longer list of bytes is a `U8`
channel per register. A `RegisterFile` register is a `U32`, or the signal's format.
`telemetry: false` on a model leaves its registers out.

`wstsModelTlm` is a passive `WstsAdapted.ModelTelemetry` written for the deployment
(`WstsAdapted/ModelTelemetry/` of the overlay), at the set-up anchor's base ID + 0x800. `Wsts.SimControl`
writes its channels with its own, once a second (once a simulated second in stepped time),
through `Wsts::Control::addTelemetrySource`; with `control: false` there is none, and
`generate` warns. The report lists each model's channels; telemetry packets omit them, as they
omit `wstsControl`'s.

## `Wsts.SimControl`

Commands mirror the actions (see the README); `SIM_STEP` (`ticks`) is `step` and `SIM_SET_STEPPED` (`stepped`) is `set_stepped`; `REG_SET` (`endpoint`, `offset`, `value`,
both 64 bits) and `IRQ_RAISE` (`endpoint`, `number`) are those of the register kind. Events: `SimPaused`, `SimResumed`,
`SimRateSet`, `FaultArmed`, `FaultTriggered` (first application of a fault only),
`FaultExpired`, `FaultsCleared`, `Injected`, `ScenarioLoaded`, `ScenarioStarted`,
`ScenarioCompleted`, `ScenarioStopped`, `ScenarioError`, `RecordStarted`,
`RecordStopped`, `ActionFailed`, `SimLog`. Telemetry, once a second: `SimTicks`,
`SimRunState`, `SimTimeScale`, `ActiveFaults`, `FaultHits`, `ScenarioRunState`,
`ScenarioStep`, `ScenarioSteps`, `Recording`, `BusTransactions`, `BusBytes`, `BusErrors`.

Command string arguments are limited by the project's `FW_CMD_STRING_MAX_SIZE` (40
characters by default). Name scenario files relative to `WSTS_DIR`, and send long payloads
through the bridge or a scenario file.
