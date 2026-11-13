# Reference

## Command line

| Command | Purpose |
|---|---|
| `fprime-wsts generate <wsts.yaml> [--project DIR] [--output DIR] [--check] [--json]` | Create or refresh a simulated deployment. `--project` defaults to the manifest's `project`, else the nearest `settings.ini` above the manifest. `--check` writes nothing and exits 1 if the output is out of date |
| `fprime-wsts generate <wsts-system.yaml> [--output DIR] [--check] [--json]` | The same for every element of a [system](systems.md), plus `wsts-system.json` |
| `fprime-wsts run [DIR] [--gds] [--gds-gui] [--gds-port P] [--gds-gui-port P] [--gds-gui-addr A] [--scenario F] [--scenario-mode stimulus\|responses] [--record F] [--duration S] [--bridge ADDR] [--files-dir DIR] [--env NAME=VALUE]... [--json]` | Run a built simulated deployment, optionally with the GDS. `--gds` is headless; `--gds-gui` starts the browser GUI (implies `--gds`) and prints its URL. Logs go to `DIR/wsts-logs`. `--env` sets an environment variable of the deployment, over the manifest's `run.env` |
| `fprime-wsts run [SYSTEM DIR] [--gds] [--gds-gui] [--duration S] [--env [ELEMENT.]NAME=VALUE]... [--json]` | Run every element of a generated system, with their links, and optionally one GDS on the ground element with the merged dictionary. `--env NAME=VALUE` goes to every element, `--env payload.NAME=VALUE` to one |
| `fprime-wsts check-host <DIR or manifest> [--project DIR] [--json]` | List what the host must provide for the simulated build (system headers, libraries, packages, SDK files, sanitizer runtime) and whether it does, with the host and flight compilers. Compiles nothing of the project. Exit status 1 when something is missing; a check that cannot be made is a warning. Given a generated directory it also writes `wsts-host.md` and `wsts-host.json` there |
| `fprime-wsts report [DIR] [--json]` | Print a generated deployment's fidelity report, and the host check of this machine if `generate` or `check-host` left one |
| `fprime-wsts scaffold <Component> --output DIR [--project DIR] [--name NAME]` | Start a mirror surrogate for a flight component |

Errors the user can fix are printed as `fprime-wsts: error: …` with exit code 2.

`fprime-wsts run --json` prints one summary when the run ends: `deployment`, `exit_code`,
`exited_early`, `interrupted`, `seconds`, `logs`, `gds_gui`, `final_status`. When the
deployment exits on its own and its log ends with an F´ assert, the summary has
`assert: {file, line, instances}` (and `id` when the log printed a file ID, `0x…:line`,
which is resolved with `hashes.txt` of the build cache). `instances` are the instances of
the component the file belongs to, each with `kept: true` if the simulation did not
substitute it. The same is printed on standard error, with the `substitute` entry to add
when a kept instance is the cause. A deployment that stops before it opens its bridge is
reported the same way, with `error` in the summary and exit code 2.

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
deployment: Payload              # deployment directory (the one containing Top/)
topology: Payload.Payload        # only needed if Top/ defines more than one topology
output: wsts/Payload              # default: wsts/<deployment> in the simulation project, else in the flight project

keep: [comDriver]                # instances never substituted (see "Instance names")
substitute:                      # explicit choices; override the catalog
  - instance: radioUart          # an instance name ...
    with: Wsts.SimByteStream
    endpoint: radio              # bus endpoint name; default is the instance's base name.
                                 # `endpoint: none`: the surrogate does not use the bus
    options: "chunk=256"         # passed to the surrogate's wstsBind()
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
    address: 0x68                # i2c only
    params: {"0x75": "0x68"}     # each becomes model.set(key, value) at start-up
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
  - instance: SensorSim.sensorPort       # an instance of Wsts.BusByteStreamDevice
    endpoint: uart
    options: "chunk=256"         # passed to wstsServe()

clocks:                          # rates of the simulation's own cycle sources
  - instance: SensorSim.simClock         # an instance of Wsts.SimCycle, added by `simulators`
    hz: 125                              # cycles per second of simulated time, up to 100000

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
setup:                           # C++ run by the generated set-up once the surrogates are bound and the
  headers: [Proj/Dma/Dma.hpp]    # models attached, before the runtime starts. Or just `setup: |` with the text
  code: |
    Wsts::Dma::addRegion(Proj::Dma::hostBase(), Proj::Dma::physicalBase(), Proj::Dma::size());
cmake:
  extra_subdirectories: [Lib/Math]   # modules with no FPP model that components depend on
  exclude_subdirectories: [...]      # modules to leave out, wherever they are added (project, library, framework)
  depends: [Proj_Math]               # extra link dependencies of the topology module
  cache:                             # CMake cache variables, set before F´ reads the libraries
    SIM_COMPONENTS: [SerialSensorSim, Top]   # a list becomes a CMake list
    SIM_LEVEL: 2
  include: [sim.cmake]               # CMake included after all modules, before the deployment
  include_early: [options.cmake]     # CMake included before the framework and libraries are read
  stub_variants: [Components/Imager] # modules read with BUILD_TESTING set: their stub variant (see "Bridging the host")
  cxx_flags: ["-include cstring"]    # flags of the host C++ compiler; or {<module directory>: [flags]}
libraries:
  keep: [...]                        # flight libraries to build although nothing in the model uses them
  drop: [...]
  sim: [lib/sim-common]              # further libraries of the simulation (relative to the simulation project)
run:
  env:                               # environment of the simulated deployment when it is run
    SIM_PROFILE: ./profiles/calm.txt # ./ or ../ : a path relative to this file
    SIM_SEED: 42
fpp_exclude: ["*/Experimental/*"]    # FPP files to leave out of the analysis
defines: [MY_SIM_FLAG]               # compile definitions, in addition to WSTS_SIM=1
host:
  ignore: [vendor/gen.h, -lcodec]    # names the host check does not report (header, -l<library>, package, SDK entry)
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
  otherwise.
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

`devices` names adapter instances. `Wsts.BusByteStreamDevice` offers `Drv.ByteStreamDriver`
(with `allocate` and `deallocate`) to a simulator and is the device on a byte stream
endpoint. The endpoint must be bound by a surrogate and be a byte stream, and cannot also
have a `bytestream` model, another device or a system link. `wstsServe` options:
`chunk=<bytes>`, the largest buffer per `recv` (default 1024).

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
- **`BUILD_TESTING` for the whole build** (`cmake.cache`, the flight root `CMakeLists.txt`,
  the flight `default_cmake_options`) is a warning that says what it turns on.
- **`host.ignore`** names what `check-host` should not report.
- **The host check** reads `host_requirements` of the report, which `generate` writes from
  text alone, so that it is the same on every machine: `modules`, `system_headers:
  {<header>: [{module, file, line, condition?}]}`, `libraries`, `packages: {<name>:
  {required, components, needed_by}}`, `sdk_paths`, `search` (include and link directories
  from the simulation's CMake files, `cxx_flags`), `sanitizers`, `flight_toolchain`,
  `flight_compiler`, `ignore`. What a machine answers is in `wsts-host.json` beside the
  report (`compiler`, `flight_compiler`, `missing: [{kind, name, needed_by, remedies,
  hint?}]`, `notes`, `warnings`, `ok`); that file is not compared by `--check` and is not
  meant to be checked in.

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
lists them as `configure_options`, and the build command that `generate` prints has them.

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

Stock components that are not surrogates, for instances the simulation adds:
`Wsts.BusByteStreamDevice` (the device on a byte stream endpoint, see `devices`) and
`Wsts.SimCycle` (a cycle source with its own rate, see `clocks`).

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

## Bridge protocol

Newline-delimited JSON. A client sends `{"id": n, "op": …}` and receives
`{"id": n, "ok": true, …}` or `{"id": n, "ok": false, "error": "…"}`. Beyond the actions:

| `op` | Purpose |
|---|---|
| `status` | Tick, run state, fault and scenario state, bus counters, endpoint names, and `links` / `links_up` (link ends of a system element, and how many are connected) |
| `subscribe` | Start receiving `{"ev": "rec", …}`, `{"ev": "action", …}` and `{"ev": "sim_event", …}` |
| `gpio_get` (`endpoint`) | Level flight software last wrote |
| `attach` (`endpoint`, `kind`: `i2c`\|`spi`, `addr`) | Become the device model. The sim then sends `{"ev": "i2c_req", "rid", "endpoint", "addr", "write", "rlen"}` and waits up to 2 s for `{"op": "reply", "rid", "status", "data"}` |
| `attach` (`endpoint`, `kind`: `register`) | Become the register block. The sim sends `{"ev": "reg_req", "rid", "endpoint", "access": "read", "offset": "0x…", "width", "rlen"}` or `{…, "access": "write", "offset", "width", "value": "0x…", "rlen": 0}` and waits up to 2 s for `{"op": "reply", "rid", "status", "data"}`, where `data` is the value read as little-endian hex (`width` bytes; empty for a write). Raise the block's interrupts with `irq_raise`. When the client disconnects, the block that was on the endpoint before answers again |

Byte stream and GPIO models need no `attach`: subscribe, watch `bs_tx` / `gpio_out`
records, and answer with `inject_bytes` / `gpio_force`.

`fprime_wsts.bridge.WstsBridge` wraps all of this (`serve_i2c`, `serve_spi`,
`serve_register`, `raise_irq`, `reg_set`, `register_accesses`). `fprime_wsts.bridge.WstsSystem` holds one
`WstsBridge` per element of a system and addresses faults by link name.

## Environment variables read by a simulated deployment

| Variable | Meaning |
|---|---|
| `WSTS_BRIDGE` | Bridge address, overriding the manifest |
| `WSTS_SCENARIO`, `WSTS_SCENARIO_MODE` | Scenario to load and start at start-up |
| `WSTS_RECORD` | File to record to from start-up |
| `WSTS_DIR` | Base directory for relative scenario and recording paths |
| any in `run.env` or `--env` | Set by `fprime-wsts run` and the pytest plugin for the simulator components' phase code |
| `WSTS_LINK_DIR` | Directory of the link sockets of a system (`<link>.sock`). Set by `fprime-wsts run`; without it links stay down |

## `Wsts.SimControl`

Commands mirror the actions (see the README); `REG_SET` (`endpoint`, `offset`, `value`,
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
