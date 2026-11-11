# fprime-wsts

A Workstation Test Set (WSTS) toolkit for [F´](https://fprime.jpl.nasa.gov/): it runs
flight software on a developer workstation against simulated hardware. It takes an
existing F´ deployment and produces a new deployment that runs natively on a Linux host,
with the hardware-facing components replaced by simulation surrogates. The flight
deployment is not modified, and it does not have to target Linux: a VxWorks deployment
converts the same way.

The name comes from the Workstation Test Set used on the Mars Science Laboratory mission,
the software-only test environment that is the spiritual ancestor of this idea.

It is meant for integration testing in CI, closed-loop development by people or agents,
and systems and operations testing.

```
flight components ── Drv.I2c / Spi / Gpio / ByteStream / Tick ports ──► Wsts surrogate components (F´)
                                                                              │
                                                                         Wsts bus  ◄── faults, recorder
                                                                    ┌─────────┴──────────┐
                                                     in-process C++ device models     socket bridge
                                                                                         │
                                 Wsts.SimControl commands ──► control core ◄── Python client: models, faults,
                                 scenario files ──────────►                    injection, observation
```

## What you get

- **Surrogate components** for the standard driver interfaces: `Wsts.SimByteStream` (UART),
  `Wsts.SimI2c`, `Wsts.SimSpi`, `Wsts.SimGpio`, `Wsts.SimTick`. They are ordinary F´ components,
  and projects write their own the same way.
- **`fprime-wsts generate`**: finds the hardware instances in a topology, retypes them to
  surrogates, wires in the WSTS control component, and writes a buildable overlay project.
  Hand-written topology code is reused unchanged.
- **Simulation control**: pause, resume and time scale; fault injection on any bus endpoint;
  direct input/output injection; scenario files; recording and replay. The same actions are
  available as F´ commands (`Wsts.SimControl`, with its own events and telemetry), over a
  socket bridge, and in scenario files.
- **A Python client and launcher** for tests and agents: `fprime-wsts run`, the `WstsBridge`
  class, and pytest fixtures.
- **A fidelity report** for every generated deployment, stating what was replaced and what
  the simulation does not reproduce.

## Quick start

Requirements: Linux, CMake, a C++ compiler, Python 3.9 or later.

```bash
git clone --recurse-submodules <this repository>
```

```bash
python3 -m venv venv && . venv/bin/activate
```

```bash
pip install -r lib/fprime/requirements.txt ./python
```

Convert the Linux example, build it and run it:

```bash
fprime-wsts generate examples/linux-demo/LinuxDemo/wsts.yaml
```

```bash
cd examples/linux-demo/wsts/LinuxDemo && fprime-util generate && fprime-util build
```

```bash
fprime-wsts run . --gds
```

`examples/linux-demo/LinuxDemo` is a flight deployment for a Linux single-board computer:
an IMU on I2C, a serial temperature sensor, a heater pin and an over-temperature switch.
Its manifest (`wsts.yaml`) names no drivers. The five hardware instances are found from the
surrogate catalog; the manifest only says which device models sit behind the buses.

`examples/vx-demo/VxDemo` targets VxWorks. It uses the `fprime-vxworks` UART driver and
watchdog timer and has no `main()`. `fprime-wsts generate examples/vx-demo/VxDemo/wsts.yaml`
produces a deployment that builds and runs on Linux with no VxWorks toolchain installed.

## Driving a simulation

From Python (tests, scripts, agents):

```python
from fprime_wsts.bridge import WstsBridge

with WstsBridge("tcp:50100") as sim:
    sim.subscribe()
    sim.fault_set("imuBus", "error", arg=3, count=5)   # five failed I2C transactions
    sim.inject_text("sensorUart", "$TEMP,48.0\n")       # bytes "from the device"
    sim.gpio_force("switchPin", True)                   # drive an input pin
    sim.advance(20)                                     # wait 20 simulation ticks
    print(sim.gpio_get("heaterPin"), sim.status())
```

From the ground system, with the commands on the `wstsControl` instance: `SIM_PAUSE`,
`SIM_RESUME`, `SIM_SET_RATE`, `FAULT_SET`, `FAULT_CLEAR`, `INJECT_BYTES`, `INJECT_TEXT`,
`GPIO_FORCE`, `I2C_REG_SET`, `MODEL_SET`, `SCENARIO_LOAD`, `SCENARIO_START`,
`SCENARIO_STOP`, `RECORD_START`, `RECORD_STOP`.

From a scenario file, one action per line, keyed on simulation ticks:

```json
{"tick": 20, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\n"}
{"tick": 25, "op": "gpio_force", "endpoint": "switchPin", "level": true}
{"tick": 40, "op": "fault_set", "endpoint": "imuBus", "kind": "error", "arg": 3, "ticks": 30}
```

```bash
fprime-wsts run . --gds --scenario overtemp.jsonl --record run1.jsonl
```

A recording is itself a scenario: load it again to replay the run.

## Documentation

| | |
|---|---|
| [docs/design.md](docs/design.md) | Architecture, how the transform works, roadmap |
| [docs/reference.md](docs/reference.md) | Manifest, catalog, actions, faults, scenario and bridge formats, CLI |
| [docs/fidelity.md](docs/fidelity.md) | What the simulation reproduces and what it does not |
| [docs/surrogate-guide.md](docs/surrogate-guide.md) | Writing surrogates and device models |
| [docs/vxworks-conversion.md](docs/vxworks-conversion.md) | Converting a non-Linux deployment |
| [AGENTS.md](AGENTS.md) | Working in this repository, and using the toolkit from an agent |

## Running the tests

```bash
pytest python/tests
```

```bash
fprime-util generate --ut && (cd Wsts/Core && fprime-util check)
```

End-to-end, after building a generated deployment (shown for LinuxDemo):

```bash
D=examples/linux-demo/wsts/LinuxDemo; pytest tests/e2e/test_linux_demo.py -p fprime_wsts.pytest_plugin --wsts-deployment $D --wsts-gds --dictionary $D/build-artifacts/Linux/LinuxDemo/dict/LinuxDemoTopologyDictionary.json
```

## Status

Built against F´ v4.4.2. Working today: the surrogates and transform above, real-time and
scaled simulation time, faults, scenarios, record/replay, the bridge, both examples.
Designed but not built yet: deterministic stepped time, a POSIX-backed VxWorks API shim,
file-system path remapping, substituting instances defined inside subtopologies. See
[docs/design.md](docs/design.md#roadmap).
