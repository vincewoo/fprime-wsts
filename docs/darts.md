# DARTS Coupling (design draft)

**Status: draft, nothing built.** This draft couples a DARTS/Dshell dynamics simulation to an
F´ deployment that runs under fprime-wsts. The target is a rotorcraft project modelled on
JPL's HeliCAT-DARTS and Ingenuity. It builds on the plants of
[Dynamics Simulators](dynamics.md) (phases 0 to 4, built). The choices under
[What to decide](#what-to-decide) are open, and the answers decide the build order.

## The problem

A rotorcraft's flight software closes its loops much faster than the example does. Its inputs
are also larger:

- **Control at 500 Hz.** Ingenuity's flight control is a 500 Hz callback in F´ on the flight
  computer. The example's plant exchanges at 20 Hz.
- **Sensors faster than the cycle.** Ingenuity's IMU gives gyro samples at 3200 Hz and
  accelerometer samples at 1600 Hz. The flight computer downsamples them to 500 Hz. A plant
  output today is one scalar per exchange.
- **Images.** Ingenuity's navigation camera gives 640x480 frames at 30 Hz to a second
  computer, which runs vision and the navigation filter. A plant output today is a number or
  a short text.
- **Two computers.** The flight computer and the navigation computer talk over UART.
  HeliCAT varied the navigation computer's latency in its Monte Carlo runs. In lockstep, a
  link today has a fixed latency of one receiver tick.

DARTS owns the vehicle's dynamics, its environment and its sensor models (truth, noise, bias,
camera rendering). The toolkit owns flight software, its drivers and time. The coupling has to
keep what the toolkit gives: repeatable runs in stepped time, lockstep across elements, exact
replay, and snapshots.

## How DARTS couples to flight software

DARTS couples flight software at a fixed I/O step equal to one flight software cycle. That is
the same shape as the toolkit's plant exchange. How HeliCAT ran Ingenuity's F´ software is not
published.

| Source | What it says about flight software integration |
|---|---|
| [Dshell, DASC 1997](https://portal.karanadyn.com/static/pdf/dasc97.pdf) | Each tick is an I/O step. In closed loop, "the size of an I/O step is set to the length of one flight software RTI", made of whole integration steps. Models take commands in `startIoStep()` and send outputs in `endIoStep()`. Flight software attaches through "a small main() routine" that exchanges data and advances time, or by embedding Dshell. Models finish each tick, which gives the determinism closed loop needs |
| [DSENDS, AIAA 2016](https://portal.karanadyn.com/static/pdf/2016-aiaa-dsends.pdf) | C++ models joined by signals, built from Python (SWIG) as nested assemblies. Dshell "can be run as a server, with an external process controlling" it, or as a client, or as a C++ library embedded elsewhere |
| [DSENDS, SpaceOps 2012](https://portal.karanadyn.com/static/pdf/2012-SpaceOps-Dsends.pdf) | Run scripts in Python: `SimulationExecutive()`, `createAssemblies(config)`, `bindState()`, `resetState(0.0)`, then `step()` in a loop. DVar is a URL-like address for every body, joint, parameter, state and signal, for reading, writing and logging |
| [HeliCAT-DARTS, Aerospace 2024](https://portal.karanadyn.com/static/pdf/2024-helicat-mdpi.pdf) | An "FSW interface" model sends sensor data and receives control commands; "HeliCAT itself is not the FSW". Sensor models: IMU, altimeter, inclinometer (truth, noise, bias), and a camera (CAHVORE model, IRIS ray tracing) |
| [Flight Control System for NASA's Mars Helicopter, 2019](https://portal.karanadyn.com/static/pdf/2019-mars-heli.pdf) | Flight control is C++ "structured as a set of callbacks within the F’ flight software framework". It runs at 500 Hz on the flight computer, with vision and navigation on a Snapdragon (Linux) over UART. Rates: gyro 3200 Hz, accelerometer 1600 Hz, laser rangefinder 50 Hz, camera 30 Hz |
| [In-flight System Identification of Ingenuity, 2025](https://rotorcraft.arc.nasa.gov/Publications/files/Aagren_AIAA_2025.pdf) | "The final frequency sweeps were run with the full integrated flight software in HeliCAT" |
| [EELS-DARTS, Aerospace 2024](https://doi.org/10.3390/aerospace11100795) | Closes the loop with the autonomy stack over ROS topics. "It is common for DARTS simulators to interface with the full flight software (FSW) stack during later stages of a project" |

No source says whether HeliCAT ran F´ in its own process or in another, which rate drove the
exchange, whether drivers ran, or whether runs repeat. One likely reading, not checked: the
classic Dshell pattern, with an FSW interface model exchanging once per 500 Hz cycle and the
navigation computer's latency modelled.
[Karana Dynamics' kdFlex](https://www.karanadyn.com/products/kdflex) is the commercial
successor, built on the same Spatial Operator Algebra. It has a C++ and Python API and says it
accepts external control stacks. Its interfaces are not confirmed here.

## What the toolkit gives today

A plant is stepped by the lockstep conductor between instants (`Coupling.exchange`,
`lockstep.Conductor._couple`). It reads what flight software commanded, steps to the next
instant's time, and writes the outputs that changed as one bridge `batch` per element. The
run is journaled, replays exactly and records the same bytes twice. The plant kinds are
`python`, `fmu`, `socket` and `fortytwo`.

| Limit today | Why it matters for DARTS |
|---|---|
| One exchange per lockstep instant, at most the flight tick rate | A 500 Hz control cycle needs a 500 Hz tick and an exchange every 2 ms |
| About 1 ms of bridge requests per exchange (reads, then a `batch`), plus a grant per element per instant | At 500 instants a second this is near real time at best (to be measured: phase 0) |
| One scalar per output per exchange | A 3200 Hz gyro gives 6 to 7 samples per 2 ms exchange |
| Actions are JSON lines; bytes travel as hex or text | A 640x480 8-bit frame is 307,200 bytes, 30 times a second: 9.2 MB/s raw, twice that as hex |
| Every action is a journal line | Samples and frames written inline would make journals very large |
| In lockstep, a link delivers at the receiver's next tick | A fixed latency; the `delay` fault waits in host time, which breaks repeatability |
| Inputs held over a step, one instant of latency | The same as Dshell's I/O step. Nothing to change |

## Options

### The DARTS plant

| | A. `python` plant (or a `darts` kind on top of it) | B. `socket` plant over a DARTS run script | C. Dshell inside the deployment (C++) |
|---|---|---|---|
| Where DARTS runs | In the conductor's Python process | In a process of its own: its own Python, or another machine | In one element's process, on its tick thread |
| Needs | DARTS's Python modules importable from the toolkit's Python (3.13 here) | A small run script that speaks the JSON protocol of `plant_socket.py` (`start`, `step`, `stop`) | Dshell's C++ libraries linked into the overlay |
| Cost per exchange | A Python call | A socket round trip (about 0.1 ms locally) | Microseconds |
| Systems, journal, restore | As every plant | As every plant | One element only; fights the journal ([Options](dynamics.md#options)) |

In A or B, `create()` (or the run script) builds the simulation from the project's
assemblies: `SimulationExecutive`, `createAssemblies`, `bindState`, `resetState`. Then
`step(t, dt, inputs)` writes flight software's commands into model inputs by DVar address,
calls `sim.step()` over `dt` in whole integration steps, and reads the sensor outputs by DVar.
The manifest names inputs and outputs by DVar path, as 42's are named by its variable names.

**Recommendation: B first, then A where DARTS imports cleanly.** B needs nothing from DARTS's
Python environment and keeps a crash in DARTS out of the conductor. Both share the run script.
A `darts` kind adds DVar names, a check of the paths when the plant starts, and a stand-in for
tests (as `fake42` is for 42).

### Rate

At a 500 Hz tick every instant is an exchange. Today an instant costs a grant per element and,
before it, the plant's reads and writes: three round trips or more.

| | What it does | Gain | Cost |
|---|---|---|---|
| R1. Exchange in the grant | The `advance` request carries the plant's actions to apply before the tick, and the queries to answer after it. One round trip per element per instant | Removes the separate reads and `batch` | A change to `advance` in `Bridge.cpp` and the conductor. The read must happen after the tick's work is quiescent, which `advance` already waits for |
| R2. Exchange at a `step` of its own | A plant already takes `step`; a coarser exchange than the tick | Fewer exchanges | Not for a 500 Hz loop: the control cycle needs fresh IMU data every cycle |
| R3. Multi-tick grants | Grant several ticks at once when no plant or link is due | Fewer grants for slow elements | Nothing for the 500 Hz element itself |
| R4. In-process plant | Option C above | Fastest | One element, and the journal problem |

**Recommendation: R1, measured in phase 0 before it is built.** R3 helps a system whose
navigation computer ticks slower than the flight computer.

### Batched samples

A plant output may be a list of time-tagged samples: `[(t, [gx, gy, gz]), …]`, all in
`(t_k, t_k+1]`. The output mapping packs each sample into bytes (a `pack` format such as
`"<hhh"`, after `scale`), and writes them in one action into a FIFO device model that flight
software's driver drains, as it drains the real part's FIFO.

- A stock model, `Wsts::SampleFifo`, with a register view for SPI or I2C (a level register,
  a data register that pops, a watermark interrupt on `<endpoint>:irq`) and a byte stream view
  for UART sensors. A project's own IMU model can take the same `fifo.push` key instead.
- The model releases each sample when `Control::elapsedNs()` reaches its time tag, so the FIFO
  level and the data-ready interrupt move within the instant as on the real part.
- One action per exchange and FIFO: `model_set` with `key: fifo.push` and hex bytes.
  At 7 samples of 12 bytes every 2 ms that is about 84 KB/s of journal before JSON.

### Images

- **A frame store.** The plant writes each frame to the run's log directory, named by its
  SHA-256 (`frames/<hash>.raw`). The action carries the hash and the frame's time, not the
  bytes: `{"op": "frame_set", "model": "navcam", "frame": "<hash>", "t": …}`. The bridge reads
  the file (every element runs on the same machine). A journal keeps the hashes, and a snapshot
  keeps the frames it names.
- **A camera model.** `Wsts::FrameSource` holds the latest frame per camera. How flight
  software reads it depends on the project's camera driver: through a surrogate of the driver
  component (written per project by port signature, as `registers.py` writes
  `Wsts.SimRegister`), through `Wsts::Dma` into the buffer the driver reads, or through a stub
  of the vendor SDK (as the example's `Imager` has).
- **Rendering stays in the plant.** IRIS, or another renderer, runs on DARTS's side.

### Latency between computers

A flight computer and a navigation computer are a two-element system joined by a link. A link
message already carries the sender's `Control::elapsedNs()`, and in lockstep
`Inbound::deliverDue` delivers what was sent before the receiver's tick. A latency per link
adds to that time before delivery:

```yaml
links:
  - name: nav
    a: fc.navUart
    b: nc.fcUart
    latency: {fixed: 0.004, jitter: 0.002, seed: 7}   # seconds, in simulated time
```

The jitter is drawn from a hash of the seed and the message's index, as random signals are
(`Signal.hpp`), so two runs give the same delays. A Monte Carlo run varies the seed.

## What changes for a user

A sketch, to be fixed by the answers below:

```yaml
dynamics:
  - name: heli
    darts:
      run: sim/heli_plant.py               # the run script: builds the assemblies
      process: socket                      # or inline (route A)
      command: /opt/darts/bin/python {script} --port {port}
    params: {seed: 1, mass: 1.8}
    inputs:                                # flight software -> DARTS, by DVar path
      .Heli/Signals/rotorCmd/upper: {element: fc, model: escUpper, key: cmd}
      .Heli/Signals/rotorCmd/lower: {element: fc, model: escLower, key: cmd}
    outputs:                               # DARTS -> flight software
      .Heli/Sensors/IMU/samples:
        - {element: fc, model: imu, key: fifo.push, samples: true, pack: "<hhhhhh", scale: 100}
      .Heli/Sensors/LRF/range:
        - {element: fc, model: lrf, key: text, format: "R{:.3f}\n"}
      .Heli/Sensors/NavCam/frame:
        - {element: nc, model: navcam, frame: true}
```

```bash
fprime-wsts run wsts/Heli --stepped --duration 120     # DARTS stepped with the system
```

## Phases

| Phase | Delivers | Tests |
|---|---|---|
| 0. Draft and spike | This draft. A spike: a stand-in Dshell-style plant (an I/O step of whole integration steps, DVar-like names) coupled to the example at a 500 Hz tick, measuring wall time per simulated second, with and without R1 sketched outside the toolkit | The spike's numbers here |
| 1. DARTS plant | A `darts` kind on route B (and A where it imports), DVar names, a run script template | Python tests against a stand-in (`python/tests/darts/`); a test against real DARTS when `WSTS_DARTS` names it, as `WSTS_42` does |
| 2. Rate | R1: the exchange carried in the grant; R3 if a system needs it | C++ unit tests for `advance`; Python conductor tests; an e2e speed check |
| 3. Batched samples | List-valued outputs, `pack`, `Wsts::SampleFifo` | Core unit tests for the model; Python tests for the conversion; e2e: flight software drains every sample, two runs the same, a restore re-drives |
| 4. Images | The frame store, `frame_set`, `Wsts::FrameSource`, one way into flight software | Core and Python tests; e2e with a small synthetic frame source |
| 5. Link latency | `latency` on links in lockstep, seeded | Core unit tests for delivery; `test_lockstep.py`-style repeatability |
| 6. Campaigns | A Monte Carlo runner: dispersions, seeds, parallel runs, branching from snapshots, a summary | Python tests; an e2e campaign of a few runs |

The answers may reorder phases 2 to 5. Each phase keeps passing
`fprime-wsts generate examples/mission-sim/wsts-system.yaml --check`, `mkdocs build --strict`,
the C++ unit tests, `pytest python/tests` and the e2e suite.

## Risks

- **DARTS may not be available here.** It is licensed software. If it cannot run in this
  environment or in CI, tests use a stand-in, and checks against DARTS run where it is.
- **Determinism of DARTS.** A restore re-drives the plant and compares it to the bit. DARTS
  must use a fixed integrator and seeded noise, and must not read the host's clock. Threaded
  solvers or renderers (IRIS on a GPU) may not give the same bits twice.
- **Speed.** 500 Hz lockstep may stay near real time even with R1. Monte Carlo of hundreds of
  flights then needs parallel runs, not faster ones.
- **Journal size.** Batched samples add tens of KB/s. Frames are kept by hash, but a long run
  still keeps every distinct frame it was given.
- **The flight computer's target.** Ingenuity's flight computer was not a Linux or VxWorks
  machine. A deployment for another target needs a conversion first
  ([Converting Other Targets](vxworks-conversion.md)).
- **Drivers the toolkit cannot see.** A camera reached through a vendor SDK, or an IMU behind
  DMA, needs a project-specific surrogate or stub before images or samples can reach it.

## What to decide

1. **Which simulator?** JPL's DARTS/Dshell, or Karana Dynamics' kdFlex, and which version?
   Can its Python be imported from the toolkit's Python (3.13), or does it need its own
   process? *Recommendation: route B (its own process) first, whatever the answer.*
2. **What to couple?** A DARTS run script, or the DVar paths of the actuator commands in and
   the sensor outputs out.
3. **Rates.** The flight software's control cycle, each sensor's rate (IMU, rangefinder,
   inclinometer, camera), and the actuators' command rate. *Recommendation: the tick at the
   control cycle; faster sensors as batched samples.*
4. **Computers.** One, or a flight computer and a navigation computer? Which OS on each?
5. **Sensors and drivers.** Which sensors reach flight software through which drivers and
   buses (SPI IMU with a FIFO, UART rangefinder, the camera's interface)? This decides the
   device models.
6. **Images.** Is the camera in the loop (vision navigation), and does rendering come from
   DARTS? *Recommendation: if not, leave phase 4 out.*
7. **Speed.** Is near real time acceptable, or must runs be much faster (Monte Carlo of
   hundreds of flights)? *Recommendation: parallel runs for campaigns (phase 6), R1 for each
   run.*
8. **Where DARTS runs.** This cloud environment, a project machine attached to a session, or
   only inside the project network? Can CI have it, or do tests use a stand-in?
   *Recommendation: a stand-in in CI in every case.*
