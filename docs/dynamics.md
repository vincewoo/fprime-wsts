# Dynamics Simulators (design draft)

**Status: phases 0 to 4 are built.** Adapters that couple a simulation to an external
dynamics simulator, part of item 4 of the [roadmap](design.md#roadmap). The simulator is
stepped on the simulation's own time. Sensors read the simulator's state, and what flight
software commands the actuators feeds back into it. The choices under
[What to decide](#what-to-decide) were settled as recommended.
[Phase 0: what it showed](#phase-0-what-it-showed) has the results of the spike;
[Phases 1 and 2: what is built](#phases-1-and-2-what-is-built) and
[Phase 3](#phase-3-what-is-built) and [Phase 4: what is built](#phase-4-what-is-built) say what
the toolkit does now. How to use it is in the user guide ([Dynamics Simulators](user-guide/dynamics.md)).

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
| `bytes` | (Not built: what flight software sent on a byte stream, given to a decoding function) | `inject_bytes` of the `format`ted value |
| Transforms | `scale`, `offset`, `round`, `format`, `above`/`below` (a threshold, with hysteresis) | |

An output is written only when its value changes. Every write is a journal line, and most
sensors do not change every tick by a visible amount.

```bash
fprime-wsts run wsts/Mission --stepped --duration 600     # the plant is stepped with the system
```

```python
system.step(seconds=20)                        # WstsSystem: the plant steps with it
assert system.dynamics("thermal")["outputs"]["T"] > 44    # what the plant last gave
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

## Phases 1 and 2: what is built

| | |
|---|---|
| Plants | `dynamics` in `wsts-system.yaml` and in a deployment's `wsts.yaml` ([reference](reference.md#dynamics)), checked when parsed and against the generated reports (the models and endpoints named must exist), and written to `wsts-system.json` or `wsts-report.json` with each plant's path relative to the generated directory. The schemas have it |
| Kinds | `python`: a file's `create(params)` returns the plant (`step(t, dt, inputs) -> outputs`, optionally `start(t0)` and `stop()`). `fmu`: FMI 2.0 and 3.0 co-simulation through `ctypes` (`fprime_wsts.fmi`), with parameters of every scalar type set by name, the FMU's log in what an error says, a request to end the simulation or an early return refused as errors, and its state saved and loaded when it can serialise it. `build_fmu()` compiles a small FMU from C |
| The coupling | `fprime_wsts.dynamics.Coupling`, held by the `Conductor` (`lockstep.py`). Before granting an instant it reads each due plant's inputs (`gpio_get`, `model_get`, `reg_get`, requests sent together), steps the plant from its time to the instant's, converts the outputs (`scale`, `add`, thresholds with hysteresis, `round`, `format`), and writes those that changed as one `batch` per element, each action with `"via"`. Then it reads the status of the elements written to again: an edge on a pin runs flight software's handler, which may send on a link, and the next grant's `expect.<link>` must count it |
| The core | `Model::get` (the stock models answer it), `RegisterDevice::peek`, and three bridge requests: `model_get`, `reg_get` (queries, not recorded) and `batch` (several actions between the same two ticks in one round trip, each recorded as if it had come alone) |
| Runs | `fprime-wsts run <system> --stepped` steps the system's plants. A single deployment with plants, run with `--stepped`, is started in lockstep and conducted as a system of one. `WstsSystem.step()` and the pytest fixtures step them through the system's conductor, and a stepped run the MCP server starts (`run_start` with `stepped`) is conducted by `fprime-wsts run`; `WstsSystem.dynamics()` reads their last inputs and outputs; `WstsSystem(..., dynamics=False)` leaves them out. Each writes `wsts-logs/dynamics-<name>.jsonl` |
| The example | `plants/thermal.py` on Payload's heater pin, serial sensor, FPGA temperature register and over-temperature switch, in `wsts-system.yaml`. It sits at its surroundings' temperature until the heater is turned on, so the rest of the example behaves as before. The same plant as an FMI 2.0 FMU in `plants/thermal_fmu`, which computes the same bits |
| Tests | `Wsts/Core` unit tests for the queries and `batch`; `python/tests/test_dynamics.py` (manifests, conversions, a plant stepped by the conductor against stand-in elements), `test_fmi.py` (the example's FMU and an FMI 3.0 one built from C, state, errors); `tests/e2e/test_dynamics.py`: the heater loop closed on the example through a ground command, and two runs of the Python plant and one of the FMU, started hot so that flight software reacts uncommanded, recording the same bytes on both elements |

Measured on the example, on the machine it was built on: a simulated minute of the system in
lockstep in 5.4 to 5.8 s of wall time with the plant (at rest, writing nothing after its
first exchange), against 5.1 to 5.3 s without it: the reads before each instant. A plant
whose outputs change every instant adds a `batch` per element and instant, about 0.4 ms each
(phase 0).

Where it parts from the design above:

- **A plant with a `step` of its own** exchanges on a grid from where it starts (its start
  plus whole steps), not first at the next instant and then every step from there. Without
  `step` it exchanges before every instant.
- **What a plant reads is not recorded.** `model_get`, `reg_get` and `gpio_get` are queries.
  A restore gives flight software the same inputs, so the plant reads the same values again
  when it is re-driven (phase 3).
- **An FMU's library runs in the conductor's process**, as decided. The `socket` kind, which
  would isolate it, is phase 4.

## Phase 3: what is built

A coupled run is snapshotted and restored like any other, and the restored run goes on
coupled from where the snapshotted one was. The plants are re-driven, as the design proposed.

| | |
|---|---|
| A snapshot | Keeps each plant's trace (`dynamics-<name>.jsonl`) up to the snapshot's time, and names the plants in `snapshot.json` (`plants`). A single deployment with plants (conducted in lockstep: its run file says `conducted`) can now be snapshotted: it is paused and held as a system is |
| A restore | Before the journal replays, the conductor is given the traces (`Conductor.shadow_plants`). While `advance_to` grants the ticks the journal replays to, it takes each exchange again before the instant it came before: the plant reads its inputs from the replaying run and is stepped, the trace line it would write is compared with the snapshot's, bit for bit, and nothing is written (the journal applies what the plant wrote). Its output conversions follow, so a threshold's state comes back with it. At the snapshot, every exchange of the trace must have been taken (`end_shadow`); then the same conductor couples on. The restored run's trace is whole, from the start, so a snapshot of it is a snapshot like any other |
| A plant that differs | `PlantMismatch`: the plant, the exchange, and both lines. It ends the restore as a bus record that differs does |
| Where | `fprime-wsts run --from` (a system, and a single deployment with plants, restored under its conductor: `runner.restore_deployment`); the `wsts_restore` fixture, which now restores with the client's own conductor so that the test goes on with the re-driven plants (a deployment with plants is handed over as a `WstsSystem` of one element, `deployment`); the MCP server's `run_start` with `from_snapshot`, which runs `fprime-wsts run --from` |
| Tests | `python/tests/test_dynamics.py`: a plant re-driven along its own trace, writing nothing, and then writing what the original wrote at the same ticks; a trace one bit off; a restore short of its snapshot; no trace; traces cut at the snapshot's time. `tests/e2e/test_dynamics.py`: the example system with the hot-start plant snapshotted at 10 s and restored, run on to 30 s, equal to the run that never stopped in the bus records of both elements, the trace and the plant's state (the switch opens again after the restore); a plant that cools faster failing its restore at its first exchange; Payload alone, conducted with the plant, snapshotted at 8 s and restored |

**Found while building it: a race in lockstep restores.** A restore starts its journal from
outside, once the run has settled, while the tick thread may already be waiting for the
conductor's grant. `Control::awaitTick` applied a scenario's due actions only before that
wait, so a grant that came within it took the tick with the actions still unapplied, and the
restore failed its check one tick late. System restores rarely met it (more happens between
start-up and the first grant); a single deployment met it every time. After the grant,
`awaitTick` now looks again, and when actions have come due it returns without taking the
tick, so that the next call applies them and then takes the grant.
`InLockstepAScenarioStartedWhileTheTickWaitsIsAppliedBeforeTheGrantedTick` holds it.

What it does **not** do:

- **Saved plant state** (an FMU's `fmi2SerializeFMUstate`, which `fprime_wsts.fmi` can do) is
  not used by restores: re-driving needs nothing of the plant and checks itself. It would only
  save the plant's computation, which is small beside the replay.
- **Ground commands** are still not journaled. A coupled run commanded from the ground cannot
  be restored past the command; the restore's check says where.

## Phase 4: what is built

Plants outside the conductor's process, and 42.

### The spike against 42

42 (built from `github.com/ericstoneking/42` at `df4fbdf`, October 2026) was driven over its
socket interface by a client of a few dozen lines before the adapter was written.

| Question | Answer |
|---|---|
| Which interface? | Two exist. The standalone-ACS one (`AcApp`) is raw `memcpy` of doubles in a layout 42 generates per build from its `AcTypes.h`, with unframed reads: a client would have to follow the generated layout. The `TX`/`RX`/`TXRX` one is text: a `TIME` line, a line per variable (`SC[0].qn = [...]`), `[ENDMSG]`, and 4 bytes `Ack\0` back. The adapter uses the text one |
| Can it be stepped? | Yes. With `TXRX`, blocking on and FAST time, 42 sends its state after each step (`DTSIM`) and waits for a message back before the next. 1200 exchanges each moved 42's `TIME` by exactly 0.05 s |
| What does it cost? | About 1.4 ms an exchange, 42's own computation included |
| Is it the same twice? | Yes: two runs gave the same bytes |
| What can be written? | What 42's `TxRxIPC.c` reads, which 42 generates from its `Database`. In a stock 42, state: `SC[0].B[0].wn` (not `SC[0].wn`, which is derived) took effect from the next step and stayed. No actuator commands: they need 42's IPC generated with them |
| Anything else? | Without its GUI (`GUIFLAG` empty), 42 lacks `CameraModel`, which lives in a GUI file: the spike linked a stub. 42 needs its `Model/` and `World/` directories: it is run in its own directory, the case given by its path |

### What is built

| | |
|---|---|
| `socket` | `fprime_wsts.plant_socket`: a plant served by a process of its own over line-delimited JSON (`start`, `step`, `stop`; [reference](reference.md#dynamics)). The server can be started with the plant (`command`, in the manifest's directory, `{address}` and `{port}` replaced, port 0 a free one, its output in the log directory), or be running already. `python -m fprime_wsts.plant_socket` serves any Python plant: a plant out of the conductor's process, and the model for a server in another language. Numbers travel as JSON numbers, so a served plant computes the same bits as in the process |
| `fortytwo` | `fprime_wsts.fortytwo`: the case checked (FAST, no graphics, one `TXRX` server that blocks, the others `OFF`) and copied into the log directory with a free port and echo off; 42 started in its own directory; its first message (its state after its first step) read when the plant starts; each exchange a whole number of 42's steps, the inputs written as 42's lines (a vector's other components those 42 last sent; a vector 42 does not send has three components, a quaternion four), 42's `TIME` checked at every step; outputs by name and component. `prepare_case` (`python -m fprime_wsts.fortytwo`) makes a coupled case from one of 42's own |
| Basilisk | A sketch of a Basilisk plant in the user guide ([Dynamics Simulators](user-guide/dynamics.md#basilisk)). Not run: Basilisk could not be installed where this was built |
| Tests | `test_plant_socket.py`: a served plant gives the same bits as in the process, and the conductor writes the same actions with either; a Unix socket; a server that refuses, does not listen, exits, or answers nonsense; the manifest. `test_fortytwo.py`: against `fortytwo/fake42`, a stand-in that reads a case and speaks 42's protocol (cases, messages, stepping, several of 42's steps an exchange, a time that skips, 42 exiting, the conductor stepping it); and, when `WSTS_42` names the directory 42 was built in, against 42 itself: a case prepared from its `Tx`, a rate given and taken away, two runs the same |

Where it parts from the design:

- **A `fortytwo` plant cannot command actuators of a stock 42**; see above. The adapter writes
  whatever lines it is given, so a 42 whose IPC reads `SC[0].AC.Whl[0].Tcmd` takes it.
- **42's first step comes before the coupling's first exchange**, with no inputs: 42 sends
  before it reads. The plant's time 0 is 42's state after its first `DTSIM`.
- **Restores re-drive 42 like any plant**: a new 42 on the same case, re-run along the trace.

## Phases

| Phase | Delivers | Size | Proves |
|---|---|---|---|
| 0. Spike | A Python plant coupled to the example in lockstep; an FMU through `ctypes` with its state saved and restored | Small | Done ([above](#phase-0-what-it-showed)) |
| 1. Coupling (built) | `fprime_wsts.dynamics`: the `Plant` interface, `python` plants, the input and output mappings, coupling in the `Conductor` before each instant (`run --stepped`, `WstsSystem.step`, pytest, MCP all step it), `dynamics` in both manifests and their schemas, the trace file. A single deployment conducted as a system of one. The bridge's `model_get`, `reg_get` and `batch`. The example's thermal plant on Payload's heater, switch, sensor and FPGA register | Medium | The heater loop closed through flight code in an end-to-end test, and two runs the same byte for byte |
| 2. FMI (built) | The `fmu` kind: FMI 2.0 and 3.0 co-simulation through `ctypes`, parameters, a check of the model description against the manifest. The example's plant as an FMU built from C source with the example | Small to medium | A modelling tool's export runs in the loop |
| 3. Replay and snapshots (built) | The plant's actions marked (`via`). In a restore, plants re-driven in shadow mode and compared with the journal. Saved state for plants that have it. The plant's trace in the snapshot | Medium | A coupled run snapshotted and restored, and coupling resumed where it was |
| 4. Other simulators (built) | The `socket` kind and its protocol. A `42` kind after a spike against 42's IPC. A Basilisk plant as a documented example | Medium | An attitude loop against 42 or Basilisk |
| 5. Tools and docs | The MCP server's view of plants (state, trace), a user guide page, the fidelity page | Small | |

Phases 1 and 2 together made the first version; phase 3 followed by itself, without a change
to what a run journals.

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

*Settled as recommended: `python` and `fmu` first; lockstep only; plants in the conductor's
process; the toolkit's own FMI loader, co-simulation only; phases 1 and 2 as the first
version, then phase 3 (re-driving plants in shadow mode).*

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
