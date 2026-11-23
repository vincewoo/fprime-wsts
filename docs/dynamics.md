# Dynamics Simulators (design draft)

**Status: draft; phase 0 done, nothing built yet.** Adapters that couple a simulation to an
external dynamics simulator, part of item 4 of the [roadmap](design.md#roadmap). The
simulator is stepped on the simulation's own time. Sensors read the simulator's state, and
what flight software commands the actuators feeds back into it. The questions under
[What to decide](#what-to-decide) are open. [Phase 0: what it showed](#phase-0-what-it-showed)
has the results of the spike.

## The problem

Device behaviour comes from three places today:

- C++ models on the bus (`Wsts/Core/Models.hpp`, a project's own);
- Python models served through the bridge (`run.models`);
- signals of simulated time (`sine(...) as u16be`, [reference](reference.md#signals)).

All three are open loop. A signal does not know the heater is on. A model knows what flight
software wrote to it, but nothing ties that to what another device reads. Three kinds of loop
cannot be closed against real dynamics:

- an attitude loop: wheel torque changes the rates the gyro reads and the angle the sun
  sensor sees;
- a thermal loop: a heater warms a panel that a thermistor reads;
- a power loop: load and eclipse discharge a battery that the power monitor reads.

Projects already have models of their dynamics:

- spacecraft simulators such as 42 or Basilisk;
- Modelica or Simulink models exported as FMUs;
- a Python script of their own.

What they lack is a way to put flight software in the loop with those models without a
hardware testbed. The way has to keep what the toolkit already gives: repeatable runs in
stepped time, lockstep across a system's elements, exact replay, and snapshots.

## What projects use

| Simulator | What it is | Interface | Who owns time | Fit |
|---|---|---|---|---|
| **FMI co-simulation (FMU)** | A standard (2.0, 3.0) for packaging a model as a zip of a shared library and an XML description. Exported by OpenModelica, Dymola, Simulink, 20-sim, and a hundred more tools | C functions: `fmi2DoStep(t, h)`, `fmi2Get/SetReal`, `fmi2Get/SetFMUstate`, `fmi2SerializeFMUstate` (FMI 3.0: `fmi3DoStep`, the same state functions, clocks, early return) | **The importer.** The FMU integrates from `t` to `t + h` when asked, with its own solver inside | Best: built for a master that owns time. Many FMUs can save and restore their state (`canGetAndSetFMUstate`, `canSerializeFMUstate`) |
| **42** (NASA GSFC) | Attitude and orbit dynamics, environment models, sensor and actuator models. The truth model of NOS3 | Text-file configuration. A socket interface (`Inp_IPC.txt`): per socket a mode `TX`, `RX` or `TXRX`, a role (server or client), "Allow Blocking (i.e. wait on RX)", and prefixes for what it sends (`SC`, `Orb`, `World`, `SC[0].AC`). In `TXRX` it writes, then reads, every step (`42ipc.c`) | 42 itself, a fixed step (`DTSIM`). With blocking on, it waits for the other side every step, so a peer that answers once per step paces it. NOS3 also builds it with a time mode driven by NOS Engine | Good, with a step size agreed on both sides. The wire format is 42's own text: to be checked in a spike against 42 itself (phase 4) |
| **Basilisk** (CU Boulder AVS) | Astrodynamics framework, C/C++ modules scripted from Python (`pip install bsk`) | In-process Python: `ConfigureStopTime(ns)` and `ExecuteSimulation()` may be called again and again, the stop time absolute, in integer nanoseconds. Messages are read and written from Python | The script calling it | Good: a Python plant wraps it in a few lines. Its nanosecond clock matches `Control::elapsedNs()` |
| **NOS3** (NASA IV&V) | A whole environment: cFS (or F´) flight software, 42, COSMOS ground, hardware simulators on NOS Engine buses | NOS Engine is the middleware: buses (UART, I2C, SPI, CAN) and a time bus. Simulators get 42's truth from a socket (`TX` server) or from files. F´ joins through a `Nos3Time` component that reads NOS Engine ticks | NOS Engine's time driver hands out ticks (`sim-microseconds-per-tick`) | Overlaps the toolkit rather than adding to it: both want to be the bus and the clock. Its use of 42 is the part worth taking |
| **Others** | Trick (NASA JSC), JSBSim, Gazebo, Orekit, GMAT | Variable servers, sockets, Python bindings | Varies | Through the generic interfaces below (a Python plant or the socket protocol), not adapters of their own |

What these share is a **step exchange**: the caller hands over inputs and a time step, the
simulator integrates over it and hands back outputs. FMI states it most precisely. 42 in
`TXRX` with blocking, Basilisk driven from Python, and NOS3's time bus are the same
exchange in other words.

## What coupling fits stepped time

**The simulation owns time; the dynamics simulator is stepped.** In stepped time the toolkit
already decides when time advances: a tick is taken when the last one is finished. In
lockstep the conductor decides it for a whole system, instant by instant
([stepped-time.md](stepped-time.md#phase-4-what-is-built)). A dynamics simulator that kept
its own clock would race flight software as the host clock did before stepped time. So it is
stepped like an element:

```text
instant k is finished: every element is quiescent, nothing is under way
  read the inputs       what flight software commanded (pins, registers, model values)
  step the plant        from t_k to t_k+1, inputs held over the step
  write the outputs     what sensors will see, as bridge actions
take instant k+1        flight software reads the outputs and commands again
```

Between two instants nothing moves, so the inputs read are those of the end of instant k,
in every run. The outputs are applied as actions between two instants, as a bridge client's
are, and so land in the same place in every run. The plant's arithmetic is deterministic
for one build on one machine. Two runs then record the same bytes, which phase 0 showed.

What this coupling is:

- **Sampled, with one instant of latency.** A command flight software gives during instant k
  acts on the plant from t_k on, and its effect is seen from instant k+1. A digital control
  loop works the same way: actuators are commanded once a cycle and held. A loop that needs
  a faster exchange needs a faster tick.
- **Zero-order hold on inputs.** An input is constant over a step. A pulse narrower than a
  tick (a PWM heater, a thruster pulse) must be given to the plant as a duty cycle or an
  impulse by the input mapping, not as levels sampled once a tick.
- **On the system's time base.** In a system the plant is stepped once an instant, however
  many elements are due at it, and may read inputs from and give outputs to any element: a
  torque commanded by one computer and a gyro read by another close one loop.

### Replay and snapshots

Exact replay and snapshots ([snapshots.md](snapshots.md)) rest on one rule: everything that
gives flight software an input passes through the bridge or a scenario, so it is journaled.
The plant's outputs are bridge actions, so they are journaled like a client's. A restore that
replays the journal gives flight software exactly what the plant gave it, **without the plant
running**.

But a restored run that is to go on coupled needs the plant at the snapshot's instant too.
Two ways:

- **Re-drive the plant during the restore** (shadow mode). The plant is started with the
  restored run and stepped at every instant of the replay, on the inputs flight software
  gives it again. Its outputs are not applied: the journal applies them. The adapter
  compares its outputs with the journaled actions it made in the saved run, and the first
  that differs fails the restore, as a bus record that differs does now. At the end the
  plant is where it was, and coupling resumes. This works for any deterministic plant and
  costs its computation.
- **Save the plant's state with the snapshot.** FMUs that can serialise their state
  (`canSerializeFMUstate`) can be restored directly: phase 0 did it. 42 and most Python
  plants cannot.

The journal must tell the plant's actions from a client's for the first way. An action
written by a coupling carries `"via": "<plant name>"`, which the recorder keeps.

## Options

| | A. In the conductor, through the bridge | B. In the deployment, as a C++ model | C. A separate plant process, its own protocol |
|---|---|---|---|
| Where the plant runs | In the Python process that conducts the run (`fprime-wsts run`, `WstsSystem`, pytest) | Inside one element's process, on its tick thread (`Model::onTick`) | A process of its own, which the conductor steps over a socket |
| Reads and writes | Bridge requests: `gpio_get`, model values, then `model_set`, `reg_set`, `gpio_force`, `inject_bytes` | Calls into `Bus` and the models directly | As A, behind a step message |
| Systems | Any element, one time base | One element only | Any element |
| Journal and restore | Outputs are actions, journaled as they are | Applied inside a tick, so recorded as scenario actions; the restore and the plant would both apply them | As A |
| Cost per instant | About 1 ms of bridge requests (phase 0) | Microseconds | As A, plus a socket round trip |
| A crashing plant | Ends the conductor | Ends the element | Ends that process; the run reports it |
| Needs lockstep | Yes, or a one-element conductor for a single deployment | No: works in stepped and real time | Yes |

**Recommendation: A, with C as one kind of plant.** The coupling lives in the conductor,
where time is already decided for the whole system, and talks to flight software only through
the bridge, so journaling, replay and the restore check keep working unchanged. Plants of
several kinds sit behind one Python interface:

```python
class Plant:
    def start(self, t0: float, params: dict) -> None: ...
    def step(self, t: float, dt: float, inputs: dict) -> dict: ...  # outputs at t + dt
    def stop(self) -> None: ...
    # optional, for snapshots without re-driving
    def save_state(self) -> bytes: ...
    def load_state(self, state: bytes) -> None: ...
```

| Kind | Delivers |
|---|---|
| `python` | A file that defines a `Plant`. Basilisk, Orekit or a script of the project's own goes here |
| `fmu` | FMI 2.0 and 3.0 co-simulation FMUs, through a loader in the toolkit (`ctypes`, no dependency: phase 0 needed nothing else). Parameters set by name before initialisation, inputs and outputs by name, saved state when the FMU allows it |
| `socket` | The step exchange as line-delimited JSON over TCP (`{"op": "step", "t", "dt", "inputs"}` → `{"outputs"}`), for a simulator in another process or language (MATLAB, a C++ simulator, a Basilisk process on another machine) |
| `42` | 42's IPC in `TXRX` mode with blocking: one exchange per step, its `DTSIM` set to the communication step. Phase 4, after a spike against 42 itself |

B stays possible for a project that wants a plant inside one element in real time, and needs
nothing new: a C++ model with `onTick` already is one. It is not the general answer, because
it cannot couple elements and fights the journal.

## What changes for a user

In `wsts-system.yaml`, or in a deployment's `wsts.yaml` without the `element` keys:

```yaml
dynamics:
  - name: thermal
    fmu: plants/Thermal.fmu          # or python: plants/thermal.py, socket: tcp:localhost:6000
    params: {C: 5.0, P: 10.0}
    step: 0.05                       # seconds per exchange; default: every instant
    inputs:                          # flight software -> plant, read after each instant
      heater: {element: payload, gpio: heaterPin}
    outputs:                         # plant -> flight software, written before the next
      T:
        - {element: payload, model: tempSensor, key: text, format: "$TEMP,{:.2f}\n"}
        - {element: payload, model: fpga, key: reg.0x0C, scale: 10, round: true}
      switch: {element: payload, gpio: switchPin}
```

| Mapping | Inputs (read) | Outputs (written) |
|---|---|---|
| `gpio` | The level flight software last wrote (`gpio_get`) | `gpio_force`, only when the level changes (an edge is an interrupt) |
| `model` + `key` | A model's value (`model_get`, new: `Model::get`) | `model_set` |
| `register` + `offset` | A register block's value (`reg_get`, new) | `reg_set` |
| `i2c` + `addr` + `reg` | A register of a register-addressed I2C model | `i2c_reg_set` |
| `bytes` | What flight software sent on a byte stream since the last exchange, given to a decoding function | `inject_bytes`, from an encoding function |
| Transforms | `scale`, `offset`, `round`, `format`, `above`/`below` (a threshold, with hysteresis) | |

An output is written only when its value changes. Every write is a journal line, and most
sensors do not change every tick by a visible amount.

```bash
fprime-wsts run wsts/Mission --stepped --duration 600     # the plant is stepped with the system
```

```python
system.step(seconds=20)                        # WstsSystem: the plant steps with it
assert system.dynamics("thermal")["T"] > 44    # the plant's state, read between instants
```

The plant's inputs and outputs at every exchange go to `wsts-logs/dynamics-<name>.jsonl`, for
plots. They are not telemetry: a channel of them would change what flight software sends.

## Phase 0: what it showed

A spike coupled a lumped thermal model of Payload's heated panel to the example system in
lockstep, outside the toolkit: a Python loop around the existing `Conductor`. The plant was
`C dT/dt = P·h − G(T − T_env)`, integrated with RK4. Between two instants the loop:

1. read the heater pin flight software drives;
2. stepped the plant over the instant;
3. wrote the serial sensor's text (`model_set` on `tempSensor`), the FPGA's temperature
   register (`model_set` on `fpga`) and the over-temperature switch (`gpio_force` on
   `switchPin`).

Two seconds in, `HEATER_SET ON` went from the GDS through Cdh and the hub. The loop held the
system until Cdh had put the command on the hub, so it was placed at an instant.

| Question | Answer |
|---|---|
| Does the loop close through flight code? | Yes. The panel reached the trip temperature at 15.70 s. The switch's edge reached `HeaterControl.switchTripped`, which turned the heater off, and the plant saw the pin low at the next instant (15.75 s). Then it cooled |
| Are two coupled runs the same? | Yes. Two runs, each in fresh processes for a simulated minute, recorded the same bytes on both elements: 15,444 records on Payload and 10,480 on Cdh. That includes 1,557 plant actions and the hub's events and telemetry. The plant's trace was identical too |
| What does it cost? | About 1 ms per instant on top of the grant: two status reads (0.47 ms), a GPIO read (0.13 ms), and 1.3 actions on average (about 0.4 ms each). A grant to the system costs about 5 ms on the machine of the spike, most of it Cdh's 125 Hz clock. The coupled minute took 16 s with a GDS attached |
| Can an FMU be driven without a dependency? | Yes. A minimal FMI 2.0 co-simulation FMU of the same plant, written in C, was zipped and stepped from Python through `ctypes` alone, at about 5 µs a step. It tripped at the same 15.70 s |
| Can its state be kept? | Yes, for an FMU that allows it. The serialised state (32 bytes) was saved at 30 s and loaded into a fresh instance. Both instances then gave the same next 30 s, bit for bit |

What it changed in the design:

- Outputs are written only when they change, which the spike did; an action costs about
  0.4 ms.
- A `batch` request of the bridge, several actions under one `ActionGate` in one round trip,
  would cut the rest. Phase 1 adds it.
- Recorded actions keep the bridge's request `id`. Two runs record the same ids only because
  the same client made the same requests. The restore check compares bus records, not actions,
  so it is not affected. The `via` field (above) is how the restore finds the plant's actions.

What it did not test:

- 42, Basilisk, or an FMU exported by a modelling tool (with a variable-step solver inside);
- a plant shared by two elements;
- a restore of a coupled run.

## Phases

| Phase | Delivers | Size | Proves |
|---|---|---|---|
| 0. Spike | A Python plant coupled to the example in lockstep; an FMU through `ctypes` with its state saved and restored | Small | Done ([above](#phase-0-what-it-showed)) |
| 1. Coupling | `fprime_wsts.dynamics`: the `Plant` interface, `python` plants, the input and output mappings, coupling in the `Conductor` before each instant (`run --stepped`, `WstsSystem.step`, pytest, MCP all step it), `dynamics` in both manifests and their schemas, the trace file. A single deployment conducted as a system of one. The bridge's `model_get`, `reg_get` and `batch`. The example's thermal plant on Payload's heater, switch, sensor and FPGA register | Medium | The heater loop closed through flight code in an end-to-end test, and two runs the same byte for byte |
| 2. FMI | The `fmu` kind: FMI 2.0 and 3.0 co-simulation through `ctypes`, parameters, a check of the model description against the manifest. The example's plant as an FMU built from C source with the example | Small to medium | A modelling tool's export runs in the loop |
| 3. Replay and snapshots | The plant's actions marked (`via`). In a restore, plants re-driven in shadow mode and compared with the journal. Saved state for plants that have it. The plant's trace in the snapshot | Medium | A coupled run snapshotted and restored, and coupling resumed where it was |
| 4. Other simulators | The `socket` kind and its protocol. A `42` kind after a spike against 42's IPC. A Basilisk plant as a documented example | Medium | An attitude loop against 42 or Basilisk |
| 5. Tools and docs | The MCP server's view of plants (state, trace), a user guide page, the fidelity page | Small | |

Phases 1 and 2 together make a usable first version. Phase 3 can follow by itself: a coupled
run is journaled from phase 1 on, and a restore replays the plant's outputs. Only the plant's
own state is lost on restore, and the restore says so.

## Risks

- **Floating point differs between machines.** A plant gives the same numbers twice on one
  build and one machine. Across compilers, libm versions or processors (FMA, x87) it may not,
  and a threshold or a formatted value can turn a last-bit difference into a different
  record. A snapshot is restored on the machine and build it was taken on, so this matters
  for comparing recordings between machines, not for restores.
- **Plants that are not deterministic.** A multi-threaded solver, a plant that reads the
  host's clock, or an FMU with a random seed it picks itself breaks repeatability. The restore
  check finds it (the plant's outputs differ from the journal). Nothing before that does.
- **Native code in the conductor's process.** An FMU or Basilisk module that crashes takes
  the conductor with it. The run ends; elements report the conductor gone. A plant can be
  moved behind the `socket` kind to isolate it.
- **The one-instant latency.** Fast loops (a wheel's speed loop at 1 kHz, a thruster pulse)
  are wrong at a 20 Hz tick. The coupling cannot hide that, and the fidelity page will say it.
- **Speed.** Bridge requests cost about 1 ms an instant, which is small beside a system's own
  instant (5 ms in the example) but large for a deployment that ticks fast and does little.
- **42's text protocol.** It is 42's own and changes with its versions. A 42 adapter pins the
  versions it was checked against.
- **Ground commands are still not journaled** ([snapshots.md](snapshots.md#phases-2-to-4-what-is-built)).
  A coupled run commanded from the ground cannot be restored past the command. That is
  unchanged by this design, but a closed loop makes ground commands more common in tests.

## What to decide

1. **Which interface first?** *Recommendation: `python` and `fmu` (phases 1 and 2)*. A Python
   plant is the general case and the easiest to test. FMI is the one standard: every modelling
   tool exports it, and it was built for a master that owns time. 42 and Basilisk follow, with
   Basilisk as a Python plant needing no adapter of its own.
2. **Exact coupling only in lockstep, or also a loose one in real time?** In real time a plant
   could step on its own thread and write when it likes: easy, and not repeatable. The
   conductor could instead pace lockstep to the wall clock (rate 1 is real time), which gives
   an operator at a GDS real time with exact coupling. *Recommendation: lockstep only, with a
   pace in the conductor as a later addition.*
3. **Where do plants run?** In the conductor's process (A), or always in a process of their
   own (C for everything). *Recommendation: in the conductor's process, with the `socket` kind
   for isolation.*
4. **FMI through the toolkit's own loader, or FMPy?** The toolkit's own (`ctypes`, phase 0)
   adds no dependency and does co-simulation only. FMPy is a well-kept library that also runs
   model-exchange FMUs with its own solvers, but it adds a dependency.
   *Recommendation: the toolkit's own, co-simulation only.*
5. **Plant state on restore: re-drive, saved state, or neither in the first version?**
   *Recommendation: re-drive in shadow mode (phase 3), and saved state where a plant offers
   it.* Re-driving works for every deterministic plant and checks itself.
6. **Scope of the first version.** *Recommendation: phases 1 and 2*, then phase 3, then 42 or
   Basilisk, whichever a project needs first.
