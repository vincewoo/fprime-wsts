# fprime-wsts: guide for agents

Toolkit that converts F´ deployments into simulated deployments that run on Linux.
Start with [README.md](README.md); the design is in [docs/design.md](docs/design.md).

## Map

| Path | Contents |
|---|---|
| `Wsts/Core/` | Plain C++: bus (byte stream, I2C, SPI, GPIO, registers), faults, control core, cycle clocks, scenario player, recorder, bridge, reference models, links between elements, `RegisterSurrogate` (the bus side of a register interface surrogate), `Dma` (address translation a project registers). No F´ dependencies |
| `Wsts/Drv/`, `Wsts/Svc/` | F´ components: surrogates (`SimRegister` is the one for register interface components), `BusByteStreamDevice` (adapter that puts an F´ simulator on a bus endpoint), `SimCycle` (cycle source with its own rate), `SimControl` (commands/events/telemetry), `SimSetup` (anchor) |
| `catalog/wsts-catalog.yaml` | Which flight components each surrogate replaces |
| `python/fprime_wsts/` | `generate.py` (transform), `fpp.py` (model loading), `flight_cmake.py` (flight CMake read as text: root `CMakeLists.txt`, nested modules, SDK links, `BUILD_TESTING` branches, the build tree), `hostcheck.py` (what the build takes from the host, and `check-host`), `manifest.py`, `simulators.py` (added F´ instances, bus devices), `registers.py` (`Wsts.SimRegister` written for a flight register interface component), `system.py` (multi-element systems), `bridge.py`, `runner.py`, `scaffold.py`, `cli.py` |
| `examples/mission/` | The example's flight project: `Cdh` (VxWorks, no `main()`, UART inside the `SensorLink` subtopology of `lib/sensor-lib`, `CONFIGURATION_OVERRIDES`, `system.fpp`) and `Payload` (Linux: I2C, SPI, UART, GPIO, an FPGA driver on the register interface component of `lib/sensor-lib`, and `Components/Imager`, which links a camera SDK and has a stub under `if (BUILD_TESTING)`), joined by a `GenericHub`. Only read by the toolkit |
| `examples/mission-sim/` | The example's simulation project: `wsts-system.yaml`, `cdh.yaml`, `payload.yaml`, simulators written as F´ components, a 125 Hz clock, a bus-less surrogate, mirror surrogates, a device model, a register block with an interrupt, a vendored library, configuration overrides. Its `wsts/Mission/` is **generated** and checked in. `examples/README.md` maps each feature to where it is |
| `mkdocs.yml`, `docs/`, `docs-site/` | The documentation site (GitHub Pages, `.github/workflows/docs.yml`): the user guide in `docs/getting-started/` and `docs/user-guide/`, the reference documents at the top of `docs/`. `docs-site/hooks.py` points links to files outside `docs/` at GitHub. Build with `pip install -r docs-site/requirements.txt && mkdocs build --strict` |
| `tests/e2e/` | End-to-end tests against the running Mission system and a GDS (`test_system.py`, `test_cdh.py`, `test_payload.py`) |
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
fprime-util generate --ut && (cd Wsts/Core && fprime-util check)   # C++ unit tests
```

```bash
fprime-wsts generate examples/mission-sim/wsts-system.yaml          # regenerate the example
```

```bash
fprime-wsts check-host examples/mission-sim/wsts/Mission             # what the host lacks, before building
```

```bash
for e in cdh payload; do (cd examples/mission-sim/wsts/Mission/$e && fprime-util generate && fprime-util build); done
```

The end-to-end commands are in the README. If `fpp-*` tools complain about the Java
version, unset `JAVA_HOME` so the bundled JRE is used.

## Rules of thumb

- Never edit files under `examples/mission-sim/wsts/`. CI runs `fprime-wsts generate --check`
  on it.
- After changing anything under `Wsts/`, rebuild both elements of the example before running
  e2e tests. Build them from the same checkout state: the GDS refuses to merge dictionaries
  whose project versions differ (`-dirty` on one of them).
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
- `fprime-util generate` fails if the build directory already exists: after regenerating
  an overlay, run only `fprime-util build`.
- Port types are nominal in FPP: a toolkit component cannot be connected where a project's
  port type is expected. `Wsts.SimRegister` is therefore written again per flight component
  (`registers.py`, into `WstsAdapted/` of the overlay), its ports found by signature. Nothing
  in the toolkit names a project's register interface component.
- A failed register read returns all ones to flight (the ports have no status) and counts in
  `BusErrors`. Interrupts of a register endpoint are the fault target `<endpoint>:irq`.
- `set(... CACHE ...)` in an overlay does not change a value an existing build directory has
  cached: after changing `cmake.cache` in a manifest, pass the value with `cmake -D` to the
  build directory or regenerate it with `fprime-util generate -f`.
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
