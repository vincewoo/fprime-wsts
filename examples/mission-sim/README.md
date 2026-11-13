# Mission simulation project

The simulation of [`../mission`](../mission), kept outside the flight project. Nothing here
is compiled into flight software, and `fprime-wsts` never writes to the flight project.
See [the example's README](../README.md) for what it shows and how to run it.

| Path | Contents |
|---|---|
| `wsts-system.yaml` | The system: elements `cdh` and `payload`, their ID windows, the `hub` link, the ground element |
| `cdh.yaml` | Manifest of Cdh: `keep`, a generated `main`, `substitute` (with `endpoint: none`), `simulators`, `devices`, `clocks`, `libraries.sim`, `cmake.cache`, `cmake.include`, `run.env` |
| `payload.yaml` | Manifest of Payload: `catalogs`, `substitute` (the register interface component, with `Wsts.SimRegister`), `models` (two on one I2C bus, a register block with an interrupt), `cmake.cache`, `cmake.depends` |
| `wsts-catalog.yaml` | This project's surrogates, searched before the toolkit's catalog |
| `library.cmake` | Builds the modules named in the cache variable `MISSION_SIM_MODULES`, and `config/` |
| `SensorSim/SerialSensorSim/` | A queued simulator behind Cdh's UART, with a command, a parameter, events and telemetry |
| `SensorSim/GyroSim/` | A model stepped at 125 Hz by a rate group of the simulation; reads its profile from `SENSOR_SIM_PROFILE` |
| `SensorSim/SimTime/` | Replacement for Cdh's time source that uses no bus endpoint |
| `SensorSim/Top/` | The subtopology: the simulators, buffer manager, parameter database, the `Wsts.BusByteStreamDevice` adapter, the `Wsts.SimCycle` clock and its rate group |
| `Surrogates/SimLinuxGpio/` | Mirror surrogate for `Drv.LinuxGpioDriver`: the flight driver's events stay in the dictionary |
| `Surrogates/SimRegisterInterface/` | Mirror surrogate for sensor-lib's `SensorLib.RegisterInterface`, on `Wsts::RegisterSurrogate`: its debug commands read and write through the bus. Built with Payload; `payload.yaml` says how to select it |
| `Models/` | `Sim::ImuModel`, an IMU whose acceleration is set in g |
| `lib/sim-common/` | A library vendored here, with module names of its own (`SimCommon_Types`) |
| `config/` | Configuration overrides for the simulated build: more rate group ports and a larger opcode table than the flight project sets |
| `sim.cmake` | CMake included in the overlay: an alias for a flight module |
| `profiles/` | Data the simulators read at start-up |
| `wsts/Mission/` | Generated. Do not edit: change the manifests or the generator and regenerate |

The mirror surrogate behaves as the flight driver does where that differs from the
general-purpose `Wsts.SimGpio`: `gpioRead` fails with `INVALID_MODE` unless the pin was
opened as `GPIO_INPUT`, and `gpioWrite` unless it was opened as `GPIO_OUTPUT`. Payload
opens `switchPin` as an interrupt pin and also reads it, so `heater.SwitchState` is never
updated here, as on the hardware.
