# The example: Mission

One example exercises everything the toolkit does. It is a spacecraft with two flight
computers joined by an F´ `GenericHub` over Ethernet, and its simulation:

```
ground  <--TCP-->  Cdh (VxWorks)  <==hub over Ethernet==>  Payload (Linux)
```

| Directory | Contents |
|---|---|
| [`mission/`](mission) | The flight project. An ordinary F´ project that knows nothing about simulation; `fprime-wsts` only reads it |
| [`mission-sim/`](mission-sim) | The simulation project: the system manifest, a manifest per computer, and all simulation code. Its `wsts/Mission/` is generated and checked in |

**`Cdh`** is the command and data handling computer, a VxWorks downloadable kernel module
with no `main()`. Its rate groups are driven by a VxWorks watchdog timer. It talks to the
ground over TCP and sends commands with opcodes from `0x20000000` up through the hub to
the payload computer. Its serial temperature sensor and UART driver are not in the
deployment: they are inside the `SensorLink` subtopology of the project library
`lib/sensor-lib`, whose phase code opens the driver. The project overrides configuration
of that subtopology and of the framework (`config/project`).

**`Payload`** is a Linux single-board computer with no ground link: its commands, events
and telemetry all travel through the hub. It has an IMU and a power monitor on one I2C
bus, the library's serial temperature sensor on a UART, a heater pin and an
over-temperature switch, and an instrument. Its timer ticks at 20 Hz, Cdh's at 10 Hz.

## Build and run

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
merged dictionary. Each element has its own bridge: `tcp:50100` for Cdh, `tcp:50101` for
Payload.

```bash
D=examples/mission-sim/wsts/Mission; pytest tests/e2e -p fprime_wsts.pytest_plugin --wsts-system $D --wsts-gds --dictionary $D/wsts-logs/MissionDictionary.json
```

runs the end-to-end tests against it.

## Where each feature is

| Feature | In the example | End-to-end test |
|---|---|---|
| Drivers found and replaced from the surrogate catalog | Payload's timer, I2C, UART and GPIO drivers; Cdh's watchdog timer and UART | `test_payload.py`, `test_cdh.py` |
| A flight instance kept real | Cdh's ground link `comDriver` (`keep`) | every test: the GDS connects through it |
| A deployment for another OS, converted without its toolchain | Cdh (VxWorks), with a `main()` generated to call its entry points (`main` in `cdh.yaml`) | `test_cdh.py::test_watchdog_timer_surrogate_cycles_rate_groups` |
| An instance inside a library subtopology | `SensorLink.uart` in `mission/lib/sensor-lib`, patched in a copy | `test_cdh.py` (the serial sensor tests) |
| Configuration overrides of the flight project | `mission/config/project`: CdhCore and SensorLink IDs, 6 rate group ports, a 36-opcode table | `test_system.py::test_merged_dictionary_has_both_elements_and_the_project_ids` |
| Configuration overrides of the simulation, which win over flight's | `mission-sim/config`: 10 rate group ports and an 80-opcode table, for the simulators | Python tests (`test_sim_project.py`) |
| A standalone simulation project | `mission-sim/`, with `project: ../mission` in each element manifest | all |
| Project surrogates in a catalog of their own | `Sim.SimLinuxGpio`, a mirror of `Drv.LinuxGpioDriver` (`wsts-catalog.yaml`) | `test_payload.py::test_mirror_surrogate_keeps_the_flight_dictionary` and the GPIO tests |
| A surrogate with no bus endpoint | Cdh's time source replaced by `SensorSim.SimTime` (`endpoint: none`) | `test_cdh.py::test_time_comes_from_the_surrogate_without_an_endpoint` |
| Stock device models | `Wsts::RegisterMapI2c` (power monitor), `Wsts::ScriptedByteStream` (Payload's sensor) | `test_payload.py::test_flight_telemetry_comes_from_models` |
| A project device model | `Sim::ImuModel` in `mission-sim/Models`, set in g | `test_payload.py::test_project_model_takes_engineering_units` |
| Two devices on one bus | IMU at 0x68 and power monitor at 0x40 on `i2cBus` | `test_payload.py::test_two_devices_answer_on_one_i2c_bus` |
| Device simulators written as F´ components | `SensorSim.SerialSensorSim` behind Cdh's UART through `Wsts.BusByteStreamDevice` (`simulators`, `devices`), with its own command, parameter, events and telemetry | `test_cdh.py` |
| A cycle source with a rate of its own | `SensorSim.simClock` at 125 Hz stepping `SensorSim.gyroSim` (`clocks`) | `test_cdh.py::test_fast_simulator_runs_at_its_own_rate` and the clock tests |
| A further library of the simulation | `mission-sim/lib/sim-common` (`libraries.sim`) | Python tests |
| CMake around the flight modules | `cmake.cache` (each element builds only its modules), `cmake.include` (`sim.cmake`), `cmake.depends` | Python tests; the build |
| Environment of the simulated deployment | `SENSOR_SIM_PROFILE` (`run.env` in `cdh.yaml`) | `test_cdh.py::test_environment_variable_reaches_the_simulator` |
| A system of several elements | `wsts-system.yaml`: two elements, their ID windows, the `hub` link | `test_system.py` |
| Faults on a link and on an element's endpoint | the `hub` link; `payload.hubDriver` | `test_system.py` |
| Pause, resume and time scale | each element, and the whole system | `test_system.py`, `test_cdh.py`, `test_payload.py` |
| Faults, injection, GPIO, I2C registers, model parameters | from the bridge and as `wstsControl` commands through the GDS | `test_payload.py`, `test_cdh.py` |
| Scenarios, recording, replay of stimulus and of device responses | Payload | `test_payload.py::test_scenario_file`, `test_record_then_replay_reproduces_the_run` |
| A device model in another process | an IMU served from Python over Payload's bridge | `test_payload.py::test_python_device_model` |
| The fidelity report | `mission-sim/wsts/Mission/<element>/wsts-report.md` | Python tests |

What the example does not show is covered by the Python tests instead: a manifest kept
inside the flight project (`test_generate.py`), `control: none`, SPI, surrogates with other
port names, and `cmake.include_early`.
