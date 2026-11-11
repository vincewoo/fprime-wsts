# fprime-wsts: guide for agents

Toolkit that converts F´ deployments into simulated deployments that run on Linux.
Start with [README.md](README.md); the design is in [docs/design.md](docs/design.md).

## Map

| Path | Contents |
|---|---|
| `Wsts/Core/` | Plain C++: bus, faults, control core, scenario player, recorder, bridge, reference models, links between elements. No F´ dependencies |
| `Wsts/Drv/`, `Wsts/Svc/` | F´ components: surrogates, `BusByteStreamDevice` (adapter that puts an F´ simulator on a bus endpoint), `SimControl` (commands/events/telemetry), `SimSetup` (anchor) |
| `catalog/wsts-catalog.yaml` | Which flight components each surrogate replaces |
| `python/fprime_wsts/` | `generate.py` (transform), `fpp.py` (model loading), `manifest.py`, `simulators.py` (added F´ instances, bus devices), `system.py` (multi-element systems), `bridge.py`, `runner.py`, `scaffold.py`, `cli.py` |
| `examples/linux-demo/`, `examples/vx-demo/` | Flight projects. `examples/*/wsts/` is **generated**: change the generator or `wsts.yaml`, then regenerate |
| `examples/subtop-demo/` | Flight project whose UART driver is inside a library subtopology (`lib/sensor-lib`), with `CONFIGURATION_OVERRIDES` and a `system.fpp`. `wsts/SubtopDemo/` is **generated**, including its copy of the subtopology under `SensorLib/` |
| `examples/subtop-demo-sim/` | Standalone simulation project for `subtop-demo` with a device simulator written as an F´ component (`simulators`, `devices`, `cmake.cache` in its manifest). Its `wsts/SubtopDemo/` is **generated** and checked in |
| `examples/hub-demo/` | Flight project with two deployments (`Cdh`, `Payload`) joined by a `GenericHub`; `wsts-system.yaml` makes them one system. `wsts/HubDemo/` is **generated** |
| `examples/linux-demo-sim/` | Standalone simulation project for `linux-demo`: manifest, project surrogate and model. Its `wsts/` is generated and not checked in |
| `tests/e2e/` | End-to-end tests against a running simulation and GDS |
| `lib/` | Pinned submodules: `fprime` (v4.4.2), `fprime-vxworks` |

## Commands

```bash
pip install -r lib/fprime/requirements.txt -e ./python     # once, in a virtual environment
```

```bash
pytest python/tests                                     # generator, manifest, systems, bridge (about three minutes)
```

```bash
fprime-util generate --ut && (cd Wsts/Core && fprime-util check)   # C++ unit tests
```

```bash
fprime-wsts generate examples/linux-demo/LinuxDemo/wsts.yaml        # regenerate an example
```

```bash
(cd examples/linux-demo/wsts/LinuxDemo && fprime-util generate && fprime-util build)
```

```bash
fprime-wsts generate examples/hub-demo/wsts-system.yaml             # regenerate the two-element system
```

The end-to-end commands are in the README. If `fpp-*` tools complain about the Java
version, unset `JAVA_HOME` so the bundled JRE is used.

## Rules of thumb

- Never edit files under `examples/*/wsts/`. CI runs `fprime-wsts generate --check` on the
  checked-in ones.
- After changing anything under `Wsts/`, rebuild the example overlays before running e2e tests.
- FPP keywords (`state`, `reg`, `text`, …) must be escaped with `$` when used as names.
- Instances of imported subtopologies are named by their own module (`SensorLink.uart`),
  not the deployment's. A substituted instance outside `Top` is patched in a copy of its
  module, which the overlay's root `CMakeLists.txt` redirects `add_fprime_subdirectory()`
  to (`docs/design.md`, "Instances inside subtopologies").
- Adding instances to a topology must not move flight port numbers. Health ping ports are
  numbered by instance name, not by position in the topology: `simulators.py` pins the
  flight targets, and `verify_flight_connections` compares the patched model with flight.
- FPP checks base IDs across every instance definition it reads, used in a topology or not.
- A component with parameters asserts in `loadParameters()` if `prmGetOut` is unconnected.
- Autocoded handlers take enum and string arguments by `const&`.
- `fprime-util generate` fails if the build directory already exists: after regenerating
  an overlay, run only `fprime-util build`.
- A new action goes in `Control::dispatch`, then `SimControl.fpp`/`.cpp`, `bridge.py`,
  `docs/reference.md`, and a test at each level.

## Using a simulation in a development loop

```bash
fprime-wsts run <generated dir> --gds --duration 30 --json
```

starts the deployment and a headless GDS, runs for 30 seconds, and prints a JSON summary
(exit code, final tick, bus counters, log directory). Exit status is non-zero if the
deployment exits on its own. Logs: `<generated dir>/wsts-logs/sim.log` and `gds.log`.

For closed-loop checks use pytest with `-p fprime_wsts.pytest_plugin`: the `wsts` fixture is
an `WstsBridge` (faults, injection, GPIO, status), and fprime-gds's `fprime_test_api`
fixture sends flight commands and asserts on events and telemetry. `tests/e2e/` shows the
patterns, including record and replay.

For a system of several elements (see `docs/systems.md`) the same command takes the system
directory, for example `examples/hub-demo/wsts/HubDemo`: every element starts, logs go to
`wsts-logs/<element>/`, and `--gds` uses the merged dictionary. In pytest use
`--wsts-system <dir>` and the `wsts_system` fixture (`tests/e2e/test_hub_demo.py`).

Things that surprise:

- Telemetry channels are downlinked about once a second with the latest value only; a
  free-running model can overwrite an injected value before it is seen.
- While the simulation is paused the flight rate groups, and so downlink, are stopped.
- Elements of a system keep separate tick counts and rates, and a pause reaches them one
  after another.
- `fprime-gds` will not start over an existing log directory; `fprime-wsts run` clears its own.
