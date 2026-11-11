# SubtopDemo simulation project, with a simulator written as an F´ component

A standalone simulation project for [`examples/subtop-demo`](../subtop-demo). The flight
project is only read.

| Path | Contents |
|---|---|
| `wsts.yaml` | The manifest: `simulators`, `devices`, `cmake.cache` |
| `SensorSim/SerialSensorSim/` | The simulator: a queued component with a command, a parameter, an event and telemetry |
| `SensorSim/Top/` | Its subtopology: the simulator, its transmit buffers, its parameter database and the `Wsts.BusByteStreamDevice` adapter |
| `library.cmake` | Builds the modules named in the cache variable `SENSOR_SIM_COMPONENTS` |
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
