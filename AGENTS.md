# fprime-wsts: guide for agents

Toolkit that converts F´ deployments into simulated deployments that run on Linux.
Start with [README.md](README.md); the design is in [docs/design.md](docs/design.md).

## Map

| Path | Contents |
|---|---|
| `Wsts/Core/` | Plain C++: bus (byte stream, I2C, SPI, GPIO, registers), faults, control core, cycle clocks, scenario player, recorder, bridge, reference models, links between elements, `RegisterSurrogate` (the bus side of a register interface surrogate), `Dma` (address translation a project registers). No F´ dependencies |
| `Wsts/Os/` | The `Os::Queue`, `Os::Task` and `Os::RawTime` of a simulated deployment (`Wsts_Os_Queue`, `Wsts_Os_Task`, `Wsts_Os_RawTime`, chosen by the overlay's `register_fprime_deployment()`): the framework's generic priority queue and POSIX task and raw time, with what stepped time needs around them (the count of outstanding messages, the turn, simulated time). `docs/stepped-time.md` |
| `Wsts/VxWorks/` | The VxWorks API shim (`shims: [vxworks]`, `docs/vxworks-shim.md`): `include/` has `vxWorks.h`, `taskLib.h`, `semLib.h`, `msgQLib.h`, `wdLib.h`, … for project code; `Kernel.cpp` tasks (as `Os::Task`s), waiting and the system clock (a `CycleClock` at `sysClkRate`); `Objects.cpp` the semaphores, queues, watchdogs and the small libraries |
| `Wsts/Drv/`, `Wsts/Svc/` | F´ components: surrogates (`SimRegister` is the one for register interface components), the bus device adapters `BusByteStreamDevice`/`BusI2cDevice`/`BusSpiDevice`/`BusGpioDevice` (each puts an F´ simulator on the endpoint of its bus kind), `SimCycle` (cycle source with its own rate), `SimTime` (the time source: host clock, or simulated time in stepped time), `SimControl` (commands/events/telemetry), `SimSetup` (anchor) |
| `catalog/wsts-catalog.yaml` | Which flight components each surrogate replaces |
| `schemas/` | JSON Schemas of `wsts.yaml` and `wsts-system.yaml`, for editors. Written by hand: a new manifest key goes there too (`python/tests/test_schema.py` compares them with the parser's key sets) |
| `python/fprime_wsts/` | `generate.py` (transform), `fpp.py` (model loading), `flight_cmake.py` (flight CMake read as text: root `CMakeLists.txt`, nested modules, SDK links, `BUILD_TESTING` branches, the build tree), `hostcheck.py` (what the build takes from the host, and `check-host`), `hostwait.py` (code in the built components that waits on the host: the report's Time section, what may keep stepped time from working), `build.py` (`fprime-wsts build`: configures and builds overlays), `survey.py` (`fprime-wsts survey` and `init`: what a flight project needs, and starter manifests; the `wsts-configure` skill's `scripts/survey.py` calls it), `manifest.py`, `simulators.py` (added F´ instances, bus devices), `registers.py` (`Wsts.SimRegister` written for a flight register interface component), `system.py` (multi-element systems), `bridge.py`, `models.py` (serves the Python device models of `run.models` for a run), `runner.py`, `lockstep.py` (the conductor of a system in lockstep, system scenarios), `scaffold.py`, `cli.py` |
| `examples/mission/` | The example's flight project: `Cdh` (VxWorks, no `main()`, UART inside the `SensorLink` subtopology of `lib/sensor-lib`, `CONFIGURATION_OVERRIDES`, `system.fpp`, and `Cdh/Heartbeat`, which calls the VxWorks kernel without guards) and `Payload` (Linux: I2C, SPI, UART, GPIO, an FPGA driver on the register interface component of `lib/sensor-lib`, and `Components/Imager`, which links a camera SDK and has a stub under `if (BUILD_TESTING)`), joined by a `GenericHub`. Only read by the toolkit |
| `examples/mission-sim/` | The example's simulation project: `wsts-system.yaml`, `cdh.yaml`, `payload.yaml`, simulators written as F´ components, a 125 Hz clock, a bus-less surrogate, mirror surrogates, a device model, a register block with an interrupt, a vendored library, configuration overrides. Its `wsts/Mission/` is **generated** and checked in. `examples/README.md` maps each feature to where it is |
| `mkdocs.yml`, `docs/`, `docs-site/` | The documentation site (GitHub Pages, `.github/workflows/docs.yml`): the user guide in `docs/getting-started/` and `docs/user-guide/`, the reference documents at the top of `docs/`. `docs-site/hooks.py` points links to files outside `docs/` at GitHub. Build with `pip install -r docs-site/requirements.txt && mkdocs build --strict` |
| `tests/e2e/` | End-to-end tests against the running Mission system and a GDS (`test_system.py`, `test_cdh.py`, `test_payload.py`, `test_stepped.py`, `test_lockstep.py`) |
| `lib/` | Pinned submodules: `fprime` (v4.4.2), `fprime-vxworks` |
| `.claude/skills/` | Agent skills: `wsts-configure` (configure, build and run a simulation of a flight project checked out beside this one, one deployment or a system), `wsts-device-model` (a device model from a datasheet and the real device). Each has scripts, references and evals |

## Commands

```bash
pip install -r lib/fprime/requirements.txt -e ./python     # once, in a virtual environment
```

```bash
pytest python/tests                                     # generator, manifest, systems, bridge (about ten minutes)
```

```bash
fprime-util generate --ut && (cd Wsts/Core && fprime-util check) && (cd Wsts/Os && fprime-util check) && (cd Wsts/VxWorks && fprime-util check)   # C++ unit tests
```

```bash
fprime-wsts generate examples/mission-sim/wsts-system.yaml          # regenerate the example
```

```bash
fprime-wsts check-host examples/mission-sim/wsts/Mission             # what the host lacks, before building
```

Build both elements of the example (configures each when it has to):

```bash
fprime-wsts build examples/mission-sim/wsts/Mission
```

The end-to-end commands are in the README. If `fpp-*` tools complain about the Java
version, unset `JAVA_HOME` so the bundled JRE is used.

## Rules of thumb

- Never edit files under `examples/mission-sim/wsts/`. CI runs `fprime-wsts generate --check`
  on it.
- After changing anything under `Wsts/`, rebuild both elements of the example before running
  e2e tests. Build them from the same checkout state: the GDS refuses to merge dictionaries
  whose project versions differ (`-dirty` on one of them). `fprime-wsts build` warns and
  `run --gds` stops with the versions when they do; `fprime-wsts build <system> --fresh`
  rebuilds them from one state.
- FPP keywords (`state`, `reg`, `text`, …) must be escaped with `$` when used as names.
- Instances of imported subtopologies are named by their own module (`SensorLink.uart`),
  not the deployment's. A substituted instance outside `Top` is patched in a copy of its
  module, which the overlay's root `CMakeLists.txt` redirects `add_fprime_subdirectory()`
  to (`docs/design.md`, "Instances inside subtopologies").
- Adding instances to a topology must not move flight port numbers. Health ping ports are
  numbered by instance name, not by position in the topology: `simulators.py` pins the
  flight targets, and `verify_flight_connections` compares the patched model with flight.
- FPP checks base IDs across every instance definition it reads, used in a topology or not.
- F´ lets the last registration of a configuration file win. Libraries are read before the
  overlay adds flight modules, so the overlay holds the simulation's override modules back
  (`WSTS_DEFERRED_MODULES`) and adds them last. The analysis root order must match.
- A flight library is kept when the model uses it or when it supplies configuration that a
  built module overrides (`kept_for_config` in `generate.py`). The example keeps
  `fprime-vxworks` for `VxWorksCfg.hpp` alone.
- `wsts-include/` is first on every module's include path. A header there must pass a
  flight driver's own module on to the real header (`WSTS_FLIGHT_DRIVER_BUILD`,
  `#include_next`): framework drivers are still compiled on the host.
- Flight CMake is never evaluated, only read (`flight_cmake.py`). Carry over literal values
  and report the rest; a reported statement names the manifest key that reproduces it.
- `BUILD_TESTING` is never set for the whole simulated build. `cmake.stub_variants` sets it
  as a variable of the one `add_fprime_subdirectory()` call that adds a listed module, with
  `__FPRIME_NO_UT_GEN__` on and the coverage flags emptied. `BUILD_UT` stays undefined: it
  changes the layout of framework classes.
- A module is read in the variant the build takes (`flight_cmake.in_variant`): SDK warnings
  and host requirements of a listed module come from its `if (BUILD_TESTING)` branch.
- The report is checked in and must be the same on every machine: `host_requirements` are
  read from text only. What one machine answers goes to `wsts-host.json` and `wsts-host.md`,
  which `--check` does not compare and git ignores.
- `cmake.cache` cannot set what `project()` has cached already (`CMAKE_CXX_FLAGS`,
  `CMAKE_BUILD_TYPE`): compiler flags go through `cmake.cxx_flags`.
- `fprime-util generate -DNAME=VALUE` takes the option directly. After `--`, or with a
  space in the value, it takes the text for a toolchain name.
- A tick source declares its period (`Control::setTickPeriod`): cycle clocks are granted
  their cycles from it on each tick. `tick` always counts flight ticks.
- A component with parameters asserts in `loadParameters()` if `prmGetOut` is unconnected.
- Autocoded handlers take enum and string arguments by `const&`.
- `fprime-wsts build <dir or manifest>` configures and builds an overlay, or every element
  of a system (`--element`, `--fresh`, `-D NAME=VALUE`, `--json`; logs in
  `wsts-logs/[<element>/]build.log`). It configures again only when what a build directory
  caches has changed, which it knows from `wsts-build.json` in the build directory. By hand,
  `fprime-util generate` fails if the build directory already exists: after regenerating
  an overlay, run only `fprime-util build`.
- Port types are nominal in FPP: a toolkit component cannot be connected where a project's
  port type is expected. `Wsts.SimRegister` is therefore written again per flight component
  (`registers.py`, into `WstsAdapted/` of the overlay), its ports found by signature. Nothing
  in the toolkit names a project's register interface component.
- A failed register read returns all ones to flight (the ports have no status) and counts in
  `BusErrors`. Interrupts of a register endpoint are the fault target `<endpoint>:irq`.
- `set(... CACHE ...)` in an overlay does not change a value an existing build directory has
  cached: after changing `cmake.cache` in a manifest, pass the value with `cmake -D` to the
  build directory or regenerate it with `fprime-util generate -f`. `fprime-wsts build` does
  the second by itself.
- Quiescence: `status` has `activity` (work flight software has been given and not finished)
  and `wait_quiescent` waits for zero and names the queues that hold work up. A message on
  a task's queue counts from send until its handler returns; one on a polled queue (a
  queued component) only while it is handled. A thread of the simulation that runs flight
  handlers itself holds an `Activity::Scope` meanwhile.
- Stepped time (`run --stepped`, `time.mode`, `set_stepped`; experimental, `docs/stepped-time.md`):
  a tick source brackets each tick with `Control::awaitTick()` and `finishTick()`, which in
  stepped time wait for the last tick's work instead of the wall clock. The bridge holds a
  `Control::ActionGate` around an action, so it falls between two ticks. A tick that does not
  finish within `WSTS_STEP_LIMIT_MS` pauses the simulation and sets `stuck` in `status`.
  In stepped time flight software is given simulated time (`Control::flightTime()`,
  `Wsts.SimTime`, `Wsts_Os_RawTime`) and runs on one thread at a time, in a fixed order
  (`Activity::enter()`/`awaitTurn()`; `Wsts_Os_Task` and the queue take the turn): two runs
  record the same bytes, which `tests/e2e/test_stepped.py` holds. Tasks are known to the
  turn from `Os::Task::start` (`Activity::expectTask`), start-up settles before the first
  tick and before any action (`Control::waitSettled`), and listeners are told at fixed
  points only (not from `onIdle`). Anything new that runs
  flight handlers on a thread of the simulation needs an `Activity::Scope`; anything that
  reads the host's clock on flight software's behalf breaks the comparison. `generate` lists calls in the built components' sources that wait on
  the host (`hostwait.py`, the Time section of the report; warnings only with
  `time.mode: stepped`). Framework components are not read: a new one that waits on the
  host goes in `hostwait.FRAMEWORK`.
- Lockstep (`run <system> --stepped`, `WstsSystem.set_stepped/step`; phase 4 of
  `docs/stepped-time.md`): an element with `Control::lockstep()` takes a tick only when
  granted (`Control::grant`, the bridge's `advance`, which is handled outside the
  `ActionGate` and blocks its connection). The conductor (`lockstep.py`) grants each instant
  to the elements due at it, in parallel, with `expect.<link>`: what the other end had sent.
  Link messages carry the sender's `Control::elapsedNs()` (tick times period, not the
  epoch); in lockstep `LinkByteStream` holds them and `Control::onTick` delivers those sent
  before the tick's time (`Inbound::deliverDue`). Anything new that carries bytes between
  elements must do the same, or two runs differ. `tests/e2e/test_lockstep.py` holds it.
- VxWorks API shim (`Wsts/VxWorks`, `shims: [vxworks]`): `OK`, `ERROR`, `NO_WAIT` are
  enumerators, not macros, as `fprime-vxworks` makes them (F´ headers use the names). Its
  headers are found only when the manifest names the shim: the header index of `generate`
  leaves `Wsts/VxWorks/include` out otherwise, so `check-host` reports them missing and
  `generate` warns. A give or send hands the object to the first waiter itself. Whatever
  wakes a waiter `Activity::begin()`s a piece of work for it, which the woken thread
  `hold()`s until it blocks again (`releaseHeld()`: the shim's blocking calls, the counting
  queue's blocking receive, thread exit). Only task threads hold (`setTaskThread()`, set by
  `Wsts_Os_Task` and the shim): for any other thread the piece ends at once. Never touch the
  turn (`Activity::enter`, `awaitTurn`, `poke`) with the kernel's mutex held: the turn asks
  waiters whether they are ready under its own mutex, and they take the kernel's.
- A new action goes in `Control::dispatch`, then `SimControl.fpp`/`.cpp`, `bridge.py`,
  `docs/reference.md`, and a test at each level.

## Using a simulation in a development loop

```bash
fprime-wsts run <generated dir> --gds --duration 30 --json
```

starts the deployment and a headless GDS, runs for 30 seconds, and prints a JSON summary
(exit code, final tick, bus counters, log directory). Exit status is non-zero if the
deployment exits on its own; if it stopped on an F´ assert, the summary has
`assert: {file, line, instances}` and the kept instance to substitute is named. Logs: `<generated dir>/wsts-logs/sim.log` and `gds.log`.

While a run is up, `fprime-wsts status <dir>` prints where it is and `fprime-wsts ctl <dir> <op>
field=value …` sends one action through the bridge (`--element` for one element of a system).
`--auto-ports` (`--wsts-auto-ports` in pytest) takes free ports for the bridge and the GDS, so
runs do not collide; the addresses in use are in `wsts-logs/wsts-run.json` while it runs.

For a GDS you can open in a browser (a human, or a browser-automation agent), use
`--gds-gui` instead of `--gds`: fprime-gds then serves its web UI on `--gds-gui-port`
(default 5000) at `--gds-gui-addr` (default `127.0.0.1`), and `fprime-wsts run` prints the
URL and reports it as `gds_gui` in `--json`. The GDS is otherwise the same one.

For closed-loop checks use pytest with `-p fprime_wsts.pytest_plugin`: the `wsts` fixture is
an `WstsBridge` (faults, injection, GPIO, status), and fprime-gds's `fprime_test_api`
fixture sends flight commands and asserts on events and telemetry. `tests/e2e/` shows the
patterns, including record and replay.

For a system of several elements (see `docs/systems.md`) the same command takes the system
directory, for example `examples/mission-sim/wsts/Mission`: every element starts, logs go to
`wsts-logs/<element>/`, and `--gds` uses the merged dictionary. In pytest use
`--wsts-system <dir>` and the `wsts_system` fixture; `wsts_system["payload"]` is one
element's `WstsBridge` (`tests/e2e/conftest.py`).

Things that surprise:

- Telemetry channels are downlinked about once a second with the latest value only; a
  free-running model can overwrite an injected value before it is seen.
- While the simulation is paused the flight rate groups, and so downlink, are stopped. In
  the example, pausing Payload alone does not stop its events: they go through the hub to
  Cdh, which keeps running.
- Events from Payload arrive late through the hub: one from before a `clear_histories()`
  can still turn up after it.
- Elements of a system keep separate tick counts and rates, and a pause reaches them one
  after another.
- `fprime-gds` will not start over an existing log directory; `fprime-wsts run` clears its own.
