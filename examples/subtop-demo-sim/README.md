# SubtopDemo simulation project, with a simulator written as an F´ component

A standalone simulation project for [`examples/subtop-demo`](../subtop-demo). The flight
project is only read.

| Path | Contents |
|---|---|
| `wsts.yaml` | The manifest: `substitute` (with `endpoint: none`), `simulators`, `devices`, `clocks`, `libraries.sim`, `cmake.cache`, `cmake.include`, `run.env` |
| `SensorSim/SerialSensorSim/` | A queued simulator behind the UART's bus endpoint, with a command, a parameter, an event and telemetry |
| `SensorSim/GyroSim/` | A model stepped at 125 Hz by a rate group of the simulation; reads its profile from `SENSOR_SIM_PROFILE` |
| `SensorSim/SimTime/` | Replacement for the flight time source that uses no bus endpoint |
| `SensorSim/Top/` | The subtopology: the simulators, buffer manager, parameter database, the `Wsts.BusByteStreamDevice` adapter, the `Wsts.SimCycle` clock and its rate group |
| `lib/sim-common/` | A library vendored here, with module names of its own (`SimCommon_Types`) |
| `config/` | Configuration overrides for the simulated build: more rate group ports and a larger opcode table than the flight project sets |
| `sim.cmake` | CMake included in the overlay: an alias for a flight module |
| `profiles/` | Data the simulators read at start-up |
| `library.cmake` | Builds the modules named in the cache variable `SENSOR_SIM_COMPONENTS`, and `config/` |
| `wsts/SubtopDemo/` | Generated. Do not edit |

```bash
fprime-wsts generate examples/subtop-demo-sim/wsts.yaml
```

```bash
(cd examples/subtop-demo-sim/wsts/SubtopDemo && fprime-util generate && fprime-util build)
```

```bash
fprime-wsts run examples/subtop-demo-sim/wsts/SubtopDemo --gds
```

With the GDS up, `SensorSim.sensorSim.TEMPERATURE_PRM_SET` changes what the flight
component `SensorLink.sensor` reports in `Temperature`.

See [Device simulators as F´ components](../../docs/surrogate-guide.md#device-simulators-as-f-components).
