# LinuxDemo simulation project

The simulation of [`../linux-demo`](../linux-demo), kept outside the flight project. Nothing
here is compiled into flight software, and `fprime-wsts` never writes to the flight project.

| Path | Contents |
|---|---|
| `wsts.yaml` | Manifest. `project: ../linux-demo` names the flight project |
| `wsts-catalog.yaml` | This project's surrogates, searched before the built-in catalog |
| `library.cmake` | Builds the modules below. The generated deployment lists this directory as an F´ library |
| `Surrogates/SimLinuxGpio/` | Mirror surrogate for `Drv.LinuxGpioDriver`: the flight driver's events stay in the dictionary |
| `Models/` | `Sim::ImuModel`, an IMU whose acceleration is set in g |
| `wsts/` | Generated deployment. A build artifact: ignored, never checked in |

```bash
fprime-wsts generate examples/linux-demo-sim/wsts.yaml
```

```bash
cd examples/linux-demo-sim/wsts/LinuxDemo && fprime-util generate && fprime-util build && fprime-wsts run . --gds
```

```bash
D=examples/linux-demo-sim/wsts/LinuxDemo; pytest tests/e2e/test_linux_demo_sim.py -p fprime_wsts.pytest_plugin --wsts-deployment $D --wsts-gds --dictionary $D/build-artifacts/Linux/LinuxDemo/dict/LinuxDemoTopologyDictionary.json
```

The mirror surrogate behaves as the flight driver does where that differs from the
general-purpose `Wsts.SimGpio`: `gpioRead` fails with `INVALID_MODE` unless the pin was
opened as `GPIO_INPUT`, and `gpioWrite` unless it was opened as `GPIO_OUTPUT`. LinuxDemo
opens `switchPin` as an interrupt pin and also reads it, so `heater.SwitchState` is never
updated here, as on the hardware.
