# fprime-wsts: guide for agents

Toolkit that converts F´ deployments into simulated deployments that run on Linux.
Start with [README.md](README.md); the design is in [docs/design.md](docs/design.md).

## Map

| Path | Contents |
|---|---|
| `Wsts/Core/` | Plain C++: bus, faults, control core, scenario player, recorder, bridge, reference models. No F´ dependencies |
| `Wsts/Drv/`, `Wsts/Svc/` | F´ components: surrogates, `SimControl` (commands/events/telemetry), `SimSetup` (anchor) |
| `catalog/wsts-catalog.yaml` | Which flight components each surrogate replaces |
| `python/fprime_wsts/` | `generate.py` (transform), `fpp.py` (model loading), `manifest.py`, `bridge.py`, `runner.py`, `scaffold.py`, `cli.py` |
| `examples/linux-demo/`, `examples/vx-demo/` | Flight projects. `examples/*/wsts/` is **generated**: change the generator or `wsts.yaml`, then regenerate |
| `examples/linux-demo-sim/` | Standalone simulation project for `linux-demo`: manifest, project surrogate and model. Its `wsts/` is generated and not checked in |
| `tests/e2e/` | End-to-end tests against a running simulation and GDS |
| `lib/` | Pinned submodules: `fprime` (v4.4.2), `fprime-vxworks` |

## Commands

```bash
pip install -r lib/fprime/requirements.txt -e ./python     # once, in a virtual environment
```

```bash
pytest python/tests                                     # generator, manifest, bridge (about a minute)
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

The end-to-end command is in the README. If `fpp-*` tools complain about the Java
version, unset `JAVA_HOME` so the bundled JRE is used.

## Rules of thumb

- Never edit files under `examples/*/wsts/`. CI runs `fprime-wsts generate --check` on the
  checked-in ones.
- After changing anything under `Wsts/`, rebuild the example overlays before running e2e tests.
- FPP keywords (`state`, `reg`, `text`, …) must be escaped with `$` when used as names.
- Autocoded handlers take enum and string arguments by `const&`.
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

Things that surprise:

- Telemetry channels are downlinked about once a second with the latest value only; a
  free-running model can overwrite an injected value before it is seen.
- While the simulation is paused the flight rate groups, and so downlink, are stopped.
- `fprime-gds` will not start over an existing log directory; `fprime-wsts run` clears its own.
