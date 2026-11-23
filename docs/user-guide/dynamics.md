# Dynamics Simulators

A device model answers on a bus. A **plant** is what the devices sense and act on: the panel
a heater warms, the spacecraft whose rates a gyro reads and wheels turn, the battery a power
monitor measures. A plant is a dynamics simulator, stepped on the simulation's own time, so
that a control loop in flight software closes against real dynamics:

```text
instant k is finished: every element quiescent
  read the inputs       what flight software commanded: a pin, a register, a model's value
  step the plant        from t_k to t_k+1, the inputs held over the step
  write the outputs     what the sensors see, as bridge actions
take instant k+1        flight software reads them and commands again
```

The plant is stepped by the lockstep conductor, between instants, so a plant needs
[stepped time](run.md). A stepped run with a plant in it is as repeatable as one without:
two runs record the same bytes. The design, and why it is built this way, is in
[dynamics.md](../dynamics.md).

A plant is one of:

| Kind | What it is |
|---|---|
| `python` | A Python file that defines `create(params)`. Basilisk, Orekit, or a model of your own go here |
| `fmu` | An FMI 2.0 or 3.0 co-simulation FMU: a model exported by OpenModelica, Dymola, Simulink, 20-sim and many others |
| `socket` | A plant served by a process of its own, in any language, over a small JSON protocol |
| `fortytwo` | NASA GSFC's 42: spacecraft attitude and orbit dynamics, environment, sensors and actuators |

## The example

The example system has one: the thermal model of Payload's heated panel
(`examples/mission-sim/plants/thermal.py`), named in `wsts-system.yaml`:

```yaml
dynamics:
  - name: thermal
    python: plants/thermal.py
    params: {C: 5.0, P: 10.0, G: 0.1, T_env: 21.5, T0: 21.5}
    inputs:
      heater: {element: payload, gpio: heaterPin}
    outputs:
      T:
        - {element: payload, model: tempSensor, key: text, format: "$TEMP,{:.1f}\n"}
        - {element: payload, model: fpga, key: reg.0x0C, scale: 10, round: true}
        - {element: payload, gpio: switchPin, above: 45, hysteresis: 5}
```

The plant reads the heater pin that `HeaterControl` drives, and gives the panel's
temperature to flight software three ways: the serial sensor's message, the FPGA's
temperature register in tenths of a degree, and the over-temperature switch, which closes
above 45 °C and opens below 40 °C. Command `Payload.heater.HEATER_SET ON` in a stepped run,
and the panel warms at about 2 K/s until the switch trips. Then `HeaterControl` turns the
heater off and reports `OverTemperature`, and the panel cools. None of it is scripted.

```bash
fprime-wsts run examples/mission-sim/wsts/Mission --stepped --gds-gui
```

## Writing a plant in Python

```python
# plants/battery.py
class Battery:
    def __init__(self, capacity_wh, v_full, v_empty):
        self.capacity, self.v_full, self.v_empty = capacity_wh, v_full, v_empty
        self.charge = capacity_wh

    def step(self, t, dt, inputs):
        # t: simulated seconds since start-up at the start of the step; dt: its length
        load_w = 2.0 + 8.0 * inputs["radio"]          # the radio's enable pin, 1.0 or 0.0
        self.charge = max(0.0, self.charge - load_w * dt / 3600)
        fraction = self.charge / self.capacity
        return {"volts": self.v_empty + (self.v_full - self.v_empty) * fraction}

def create(params):
    return Battery(**params)
```

`step` is given the inputs by name and returns the outputs by name. It may also have
`start(t0)` and `stop()`. Keep it deterministic: no host clock, no unseeded randomness, no
threads whose order matters. Otherwise two runs differ, and a restore of a snapshot fails
its check.

## Connecting it

```yaml
dynamics:
  - name: battery
    python: plants/battery.py          # relative to the manifest
    params: {capacity_wh: 20.0, v_full: 8.4, v_empty: 6.0}
    step: 1.0                          # exchange once a simulated second (default: every instant)
    inputs:
      radio: {gpio: radioEnable}
    outputs:
      volts:
        - {model: power, key: "0x02", scale: 1000, round: true}   # a register map's value
        - {gpio: lowBattery, below: 7.0, hysteresis: 0.2}
```

Inputs are read from a GPIO pin flight software drives (`gpio`), a model's value
(`model` and `key`) or a register of a register block (`register` and `offset`). Outputs go
to a pin flight software reads (`gpio`, through a threshold), a model (`model_set`), a
register (`reg_set`), an I2C register model (`i2c`) or a byte stream (`bytes`, formatted
text). `scale`, `add`, `round`, `format`, `above` and `below` (with `hysteresis`) convert
between engineering units and what the device holds. The full table is in the
[reference](../reference.md#dynamics).

An output is written only when it changes. Each write is a bridge action, recorded and
journaled with `"via": "<plant>"`. In a deployment's own `wsts.yaml` the mappings need no
`element`. In `wsts-system.yaml` each says which element it reads or writes, so one plant
can take one computer's actuator and give another's sensor what it sees.

`fprime-wsts generate` checks that the models and endpoints a plant names exist.

## An FMU

```yaml
dynamics:
  - name: thermal
    fmu: plants/Thermal.fmu
    params: {C: 5.0}                 # the FMU's parameters, by name
    inputs:  {heater: {element: payload, gpio: heaterPin}}
    outputs: {T: {element: payload, model: fpga, key: reg.0x0C, scale: 10, round: true}}
```

The FMU must be a co-simulation FMU with a library for the machine that runs it. Its input
and output variables have the names the mapping uses. The toolkit loads it with `ctypes`, so
it needs nothing installed. A model-exchange FMU (one that needs a solver from the importer)
is refused.

The example's plant is also an FMU, built from C: `examples/mission-sim/plants/thermal_fmu`.

```bash
python examples/mission-sim/plants/thermal_fmu/build.py      # Thermal.fmu beside it
```

It computes the same bits as the Python plant, and a run with either records the same
bytes. `fprime_wsts.fmi.build_fmu()` builds one from your own C sources and model description.

## A plant in a process of its own

A plant written in another language, or one you want out of the conductor's process (native
code that might crash it), is a server the conductor steps over a socket:

```yaml
dynamics:
  - name: thermal
    socket:
      address: tcp:127.0.0.1:0       # 0: a free port, chosen when the run starts
      command: [python, -m, fprime_wsts.plant_socket, plants/thermal.py, --address, "{address}"]
    inputs:  {heater: {element: payload, gpio: heaterPin}}
    outputs: {T: {element: payload, model: fpga, key: reg.0x0C, scale: 10, round: true}}
```

The command runs in the manifest's directory and logs to `wsts-logs/dynamics-thermal.log`. The
protocol is three requests, each a line of JSON with a line in answer: `start`, `step` and
`stop` ([reference](../reference.md#dynamics)). `fprime_wsts.plant_socket` serves any Python
plant this way, and is the model for a server in another language. A plant already running
somewhere is named by its address alone (`socket: tcp:sim-host:6000`).

## 42

[42](https://github.com/ericstoneking/42) simulates a spacecraft's attitude and orbit, its
environment, sensors and actuators. Its socket interface sends its state as text after every
step of its own, and, in `TXRX` mode with blocking, waits for a message back before the next:
the coupling takes that seat.

1. Build 42 (without graphics, if the machine has no display: its `Makefile`'s `GUIFLAG`).
2. Make a case the coupling can drive from one of 42's own:

    ```bash
    python -m fprime_wsts.fortytwo ~/42/Tx sim42/attitude --prefix "SC[0].qn" --prefix "SC[0].svb" --step 0.05
    ```

    It sets FAST time and no graphics, and one `TXRX` server socket that sends the variables
    whose names start with the prefixes. In the case's spacecraft file, `PASSIVE_FSW` leaves
    control to your flight software instead of 42's own.
3. Name it in the manifest:

    ```yaml
    dynamics:
      - name: attitude
        fortytwo: {case: sim42/attitude, executable: ~/42/42}
        inputs:
          "SC[0].B[0].wn[2]": {element: payload, model: imu, key: gz, scale: 0.0174533}
        outputs:
          "SC[0].svb[0]": {element: payload, model: sunModel, key: x}
          "SC[0].qn[3]": {element: payload, model: starTracker, key: q4}
    ```

(`sunModel` and `starTracker` stand for models of your own that take those values.) Inputs
and outputs are 42's variables as it writes them, a vector's component by its index.
42 applies only the lines its `TxRxIPC.c` reads. In a stock 42 that is its state (rates,
attitude, wheel momentum): enough to set a spacecraft's state from flight software's point of
view, not to command an actuator. To feed wheel torques or thruster commands to 42, generate
its IPC with them (42's `Database` folder), and name them as inputs.

The plant's step must be a whole number of 42's steps (by default it exchanges before every
instant, so make 42's step divide the tick). 42 takes one step of its own before the first
exchange, with no inputs. A run with 42 is as repeatable as one without: two coupled runs of
a case record the same bytes.

## Basilisk

[Basilisk](https://hanspeterschaub.info/basilisk/) is a Python package, so a Basilisk
simulation is a Python plant: build it in `create(params)` and advance it in `step()` with
`ConfigureStopTime()` (an absolute time, in integer nanoseconds) and `ExecuteSimulation()`.
A sketch, after Basilisk's `scenarioAttitudeFeedbackRW` (not run in the toolkit's tests, which
do not install Basilisk):

```python
# plants/attitude_bsk.py
from Basilisk.architecture import messaging
from Basilisk.simulation import reactionWheelStateEffector, spacecraft
from Basilisk.utilities import SimulationBaseClass, macros, simIncludeRW


class Attitude:
    def __init__(self, inertia=(0.02, 0.02, 0.01), dt=0.05):
        self.sim = SimulationBaseClass.SimBaseClass()
        self.sim.CreateNewProcess("dyn").addTask(self.sim.CreateNewTask("task", macros.sec2nano(dt)))
        self.sc = spacecraft.Spacecraft()
        self.sc.hub.IHubPntBc_B = [[inertia[0], 0, 0], [0, inertia[1], 0], [0, 0, inertia[2]]]
        wheels = simIncludeRW.rwFactory()
        wheels.create("Honeywell_HR16", [0, 0, 1], maxMomentum=0.05)
        self.rw = reactionWheelStateEffector.ReactionWheelStateEffector()
        wheels.addToSpacecraft("wheels", self.rw, self.sc)
        self.torque = messaging.ArrayMotorTorqueMsg()
        self.rw.rwMotorCmdInMsg.subscribeTo(self.torque)
        for model in (self.rw, self.sc):
            self.sim.AddModelToTask("task", model)
        self.sim.InitializeSimulation()

    def step(self, t, dt, inputs):
        command = messaging.ArrayMotorTorqueMsgPayload()
        command.motorTorque = [inputs["wheel_torque"]]
        self.torque.write(command)
        self.sim.ConfigureStopTime(int(round((t + dt) * 1e9)))
        self.sim.ExecuteSimulation()
        return {"rate_z": self.sc.scStateOutMsg.read().omega_BN_B[2]}


def create(params):
    return Attitude(**params)
```

Basilisk's own tasks should run at the plant's step or a divisor of it, so that a step of the
plant ends where Basilisk's tasks do.

## Running

| | |
|---|---|
| `fprime-wsts run <dir> --stepped` | Steps the plants with the system, or with a single deployment (conducted as a system of one). The summary has `dynamics` and `sim_seconds` |
| pytest, `--wsts-system <dir> --wsts-stepped` | The `wsts_system` fixture's `step()` steps the plants; `wsts_system.dynamics("thermal")` is what the plant was last given and gave |
| `WstsSystem(dir)` | The same in a script: `step(seconds=…)`, `dynamics()`; `WstsSystem(dir, dynamics=False)` leaves the plants out |

```python
def test_the_switch_trips(gds, wsts_system):
    wsts_system.pause()
    wsts_system.set_stepped(True)
    gds.send_command("Payload.heater.HEATER_SET", ["ON"])
    wsts_system.step(seconds=20)
    assert wsts_system.dynamics("thermal")["outputs"]["T"] >= 45
    assert wsts_system["payload"].gpio_get("heaterPin") is False
```

`tests/e2e/test_dynamics.py` has the full version, and a comparison of three runs.

Each plant writes `wsts-logs/dynamics-<name>.jsonl`: the time, the step, the inputs and the
outputs of every exchange, for plotting.

## What to know

- **One instant of latency.** A command given during an instant acts on the plant from the
  end of that instant, and its effect is seen from the next. A loop faster than the tick
  needs a faster tick.
- **Inputs are held over a step.** A pulse shorter than a step (PWM, a thruster firing) is
  seen as the level at the end of the instant before the step. Give the plant a duty cycle
  or an impulse instead.
- **Stepped time only.** In real time the plants are not stepped.
- **Snapshots** keep each plant's trace. A restore re-drives the plants along it while the
  journal replays, checks every exchange against it, and the restored run goes on coupled
  from where the snapshotted one was ([Snapshots](snapshots.md)). A plant that gives other
  values than it did (another file, other parameters, a host clock) fails the restore.
