# F' Workstation Test Set (WSTS)

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

## How it fits together

Every piece of a simulated deployment comes from one of four places, and the diagrams in
these documents colour them the same way:

| | Comes from | Examples |
|---|---|---|
| 🟦 | **Flight software**, used unchanged | project components, `Main.cpp`, topology code, the F´ framework |
| 🟩 | **fprime-wsts**, the same for every project | stock surrogates, bus, control core, bridge, stock device models, the generator |
| 🟧 | **Generated** by `fprime-wsts generate` | patched topology model, `WstsSetup.cpp`, `WstsCompat.hpp`, build files, fidelity report |
| 🟪 | **Written by you** for your project | manifest, project surrogates and catalogs, device models, scenarios, tests |

A simulated deployment is one Linux process. Flight software runs as in flight down to
the driver ports; below them, surrogates hand each transaction to the WSTS bus, where
faults are applied, traffic is recorded and device models answer:

```mermaid
flowchart TB
  gds["F´ GDS"]:::external
  client["Tests, scripts, agents,<br/>Python device models"]:::user
  scenarios["Scenario files"]:::user

  subgraph proc["Simulated deployment: one Linux process"]
    fsw["Flight components and topology code<br/>(ground link kept real)"]:::flight
    glue["Generated topology wiring and set-up<br/>patched FPP, WstsSetup.cpp, WstsCompat.hpp"]:::generated
    surr["Stock surrogates<br/>Wsts.SimI2c, SimSpi, SimGpio, SimByteStream"]:::toolkit
    psurr["Project surrogates"]:::user
    tick["Wsts.SimTick"]:::toolkit
    ctl["Wsts.SimControl"]:::toolkit
    bus["Wsts bus<br/>fault table, recorder"]:::toolkit
    models["Stock device models"]:::toolkit
    pmodels["Project device models"]:::user
    core["Control core<br/>run state, ticks, scenario player"]:::toolkit
    bridge["Socket bridge"]:::toolkit
  end

  gds <-->|"TCP, flight dictionary<br/>plus WSTS entries"| fsw
  fsw -->|"Drv.I2c, Spi, Gpio,<br/>ByteStream ports"| surr
  fsw -->|"project driver ports"| psurr
  tick -->|"cycles rate groups"| fsw
  tick -->|"each tick"| core
  fsw -->|"SIM_* commands"| ctl
  ctl --> core
  surr --> bus
  psurr --> bus
  bus --> models
  bus --> pmodels
  glue -.->|"binds endpoints,<br/>attaches models"| bus
  scenarios --> core
  core -->|"faults, injection"| bus
  bridge --> core
  bus <-->|"observation,<br/>remote models"| bridge
  client <-->|"JSON over TCP"| bridge

  classDef flight fill:#dbeafe,stroke:#1d4ed8,color:#0b1f4d
  classDef toolkit fill:#dcfce7,stroke:#15803d,color:#052e16
  classDef generated fill:#ffedd5,stroke:#c2410c,color:#431407
  classDef user fill:#f3e8ff,stroke:#7e22ce,color:#3b0764
  classDef external fill:#f1f5f9,stroke:#64748b,color:#0f172a,stroke-dasharray:4 3
  style proc fill:none,stroke:#94a3b8,color:#475569
```

The simulated deployment is built from an overlay project that the generator writes next
to (or outside) the flight project. The flight project is only read:

```mermaid
flowchart LR
  subgraph flight["Flight project (read only)"]
    ftop["Topology model and code<br/>Top/*.fpp, *Topology.cpp, Main.cpp"]:::flight
    fcomp["Components, F´ framework"]:::flight
  end
  subgraph yours["Simulation sources (yours)"]
    man["wsts.yaml"]:::user
    pcode["Project catalogs,<br/>surrogates, device models"]:::user
  end
  subgraph kit["fprime-wsts"]
    cat["Surrogate catalog"]:::toolkit
    gen["fprime-wsts generate"]:::toolkit
    lib["Wsts library<br/>Core, Drv, Svc"]:::toolkit
  end
  over["Overlay project wsts/&lt;Deployment&gt;/<br/>patched FPP, WstsSetup.cpp, WstsCompat.hpp,<br/>CMake files, fidelity report"]:::generated
  exe["Simulated deployment<br/>(Linux executable)"]:::neutral

  ftop --> gen
  man --> gen
  cat --> gen
  pcode --> gen
  gen --> over
  over -->|"fprime-util build"| exe
  ftop -.->|"Main.cpp and topology<br/>code compiled as is"| exe
  fcomp -.-> exe
  lib -.-> exe
  pcode -.-> exe

  classDef flight fill:#dbeafe,stroke:#1d4ed8,color:#0b1f4d
  classDef toolkit fill:#dcfce7,stroke:#15803d,color:#052e16
  classDef generated fill:#ffedd5,stroke:#c2410c,color:#431407
  classDef user fill:#f3e8ff,stroke:#7e22ce,color:#3b0764
  classDef neutral fill:#ffffff,stroke:#334155,color:#0f172a
  style flight fill:none,stroke:#94a3b8,color:#475569
  style yours fill:none,stroke:#94a3b8,color:#475569
  style kit fill:none,stroke:#94a3b8,color:#475569
```

Most projects write only the manifest and, where the stock models are not enough, a few
device models. [docs/design.md](docs/design.md) has more detailed diagrams of each part.

## What you get

- **Device simulators as F´ components**: simulators a project already has as components
  are added to the simulated topology from the manifest and put behind a bus endpoint with
  the `Wsts.BusByteStreamDevice` adapter, with their own commands, telemetry and scheduling.
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
- **Multi-computer systems**: several deployments run as one simulated system, with the
  UART or Ethernet links between them carried by WSTS, one GDS on a merged dictionary, and
  faults on the links. See [docs/systems.md](docs/systems.md).
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
pip install -r lib/fprime/requirements.txt -e ./python
```

The repository has one example, which uses every part of the toolkit
([examples/README.md](examples/README.md) maps each feature to where it is). It is the
flight software of a spacecraft with two computers joined by an F´ `GenericHub`:

```
ground  <--TCP-->  Cdh (VxWorks)  <==hub over Ethernet==>  Payload (Linux)
```

- `examples/mission` is the flight project. `Cdh` is a VxWorks downloadable kernel module
  with no `main()`, driven by a watchdog timer; its serial sensor and UART driver are
  inside a subtopology of a project library, and the project overrides configuration of
  that library and of the framework. `Payload` is a Linux single-board computer with an
  IMU and a power monitor on I2C, a converter on SPI, a serial sensor, a heater pin and an
  over-temperature switch, and no ground link of its own.
- `examples/mission-sim` is its simulation project, which only reads the flight project.
  It holds the system manifest, a manifest per computer, and all simulation code: device
  simulators written as F´ components, a model on a 125 Hz clock of its own, a mirror
  surrogate, a device model, a vendored library and configuration overrides of its own.

Generate the simulated system, build both computers and run it:

```bash
fprime-wsts generate examples/mission-sim/wsts-system.yaml
```

```bash
for e in cdh payload; do (cd examples/mission-sim/wsts/Mission/$e && fprime-util generate && fprime-util build); done
```

```bash
fprime-wsts run examples/mission-sim/wsts/Mission --gds
```

Both computers start, the hub link comes up, and one GDS commands either of them with the
merged dictionary. No VxWorks toolchain is involved at any point.

## Driving a simulation

From Python (tests, scripts, agents):

```python
from fprime_wsts.bridge import WstsBridge

with WstsBridge("tcp:50101") as sim:                   # the Payload computer
    sim.subscribe()
    sim.fault_set("i2cBus", "error", arg=3, count=5)    # five failed I2C transactions
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
{"tick": 40, "op": "fault_set", "endpoint": "i2cBus", "kind": "error", "arg": 3, "ticks": 30}
```

```bash
fprime-wsts run <generated deployment> --gds --scenario overtemp.jsonl --record run1.jsonl
```

A recording is itself a scenario: load it again to replay the run.

## Documentation

| | |
|---|---|
| [docs/design.md](docs/design.md) | Architecture, how the transform works, roadmap |
| [docs/reference.md](docs/reference.md) | Manifest, catalog, actions, faults, scenario and bridge formats, CLI |
| [docs/systems.md](docs/systems.md) | Systems of several compute elements: links, ID windows, running and controlling |
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

End-to-end, after building the simulated system (the merged dictionary is written when
the system starts):

```bash
D=examples/mission-sim/wsts/Mission; pytest tests/e2e -p fprime_wsts.pytest_plugin --wsts-system $D --wsts-gds --dictionary $D/wsts-logs/MissionDictionary.json
```

## Status

Built against F´ v4.4.2. Working today: the surrogates and transform above, real-time and
scaled simulation time, faults, scenarios, record/replay, the bridge, multi-element systems
with byte stream links, substituting instances defined inside subtopologies, the example.
Designed but not built yet: deterministic stepped time (and with it lockstep across the
elements of a system), GPIO links between elements, a POSIX-backed VxWorks API shim,
file-system path remapping. See
[docs/design.md](docs/design.md#roadmap).
