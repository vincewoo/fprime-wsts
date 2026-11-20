# Device Models

A device model is what answers on a bus endpoint: the IMU behind `i2cBus`, the GPS behind
`gpsUart`. Flight software cannot tell a model from the device; the driver above it is a
surrogate, and the manager above that is flight code.

You have four ways to provide one:

| | Where it runs | Best for |
|---|---|---|
| A **stock model** | In the process | Register-addressed I2C devices, periodic serial sensors, loopbacks, register blocks |
| A **C++ model** of your own | In the process | Devices with behaviour: engineering units, state machines, timing |
| A **Python model** over the bridge | In a process `run` starts, or in your test | Devices that are quicker to write in Python; a device a test wants to control transaction by transaction |
| An **F´ simulator component** | In the process, in the topology | Simulators you already have, or that operators command from the GDS ([Simulators](simulators.md)) |

All of them sit behind the same endpoint, so faults, recording, replay and the bridge
work the same way whichever you choose.

## Stock models

In `Wsts/Core/Models.hpp`, ready to name in a manifest:

| Class | `kind` | Behaviour | Parameters |
|---|---|---|---|
| `Wsts::RegisterMapI2c` | `i2c` | A write sets the register pointer (first byte) and stores what follows; a read returns bytes from the pointer, auto-incrementing | `"<register>": "<byte> [<byte> …]"`, e.g. `"0x75": "0x68"` |
| `Wsts::ScriptedByteStream` | `bytestream` | Sends a fixed message every `period` ticks, like a free-running serial sensor | `text` or `hex`, `period` (ticks; 0 stops it) |
| `Wsts::LoopbackByteStream` | `bytestream` | Echoes every byte flight software sends | |
| `Wsts::LoopbackSpi` | `spi` | Shifts back what was shifted in | |
| `Wsts::RegisterFile` | `register` | A block of 32-bit memory-mapped registers with masks, write-1-to-clear, read-clears and a periodic interrupt | See [Register blocks](registers.md) |

```yaml
models:
  - name: power                     # the name model_set and MODEL_SET use
    class: Wsts::RegisterMapI2c
    endpoint: i2cBus
    kind: i2c
    address: 0x40
    params:
      "0x02": "0x6D 0x60"           # bus voltage register: 14.0 V
      "0x04": "0x01 0xF4"           # current register: 500 mA
```

Several I2C devices can share one endpoint at different addresses, as the example's IMU
(0x68) and power monitor (0x40) do. `address: any` puts a device at every address no other
device on the endpoint has: a model can answer before you know the device's address. Change a parameter while running with
`model_set power 0x02 "0x5D 0xC0"` (from Python, a scenario or the `MODEL_SET` command).

### Values that change over time

A register of `RegisterMapI2c` or `RegisterFile` can be given a **signal** in place of a
number: an expression of simulated time that the model stores in the register on every tick.
Telemetry moves, and flight software's limits and filters have something to work on,
without a line of model code:

```yaml
params:
  # bus voltage: 14 V ± 0.4 V over 30 s, with a little noise, in 1.25 mV counts, 2 bytes big-endian
  "0x02": "(sine(offset=14, amplitude=0.4, period=30) + noise(sd=0.01)) / 0.00125 as u16be"
  # current: 500 mA, stepping to 800 mA after a minute
  "0x04": "step(before=500, after=800, at=60) as u16be"
```

The functions are `sine`, `square`, `triangle`, `sawtooth`, `ramp`, `step`, `noise` and
`uniform`, and `t` is simulated time in seconds ([Reference](../reference.md#signals) has
their arguments). `as <format>` says how the value is stored (`u8` … `s32`, with `be` or `le`
for an I2C register wider than a byte). A signal is checked when the deployment is
generated. Noise is a hash of the seed and the instant, not a sequence, so two runs in
stepped time see the same values.

Change one while the simulation runs; a number ends it:

```bash
fprime-wsts ctl wsts/Payload model_set model=power key=0x04 value="ramp(from=500, to=0, duration=10) as u16be"
```

### Register telemetry

Each register you give a stock register model in the manifest is also a telemetry channel,
with the register's current value: `Payload.wstsModelTlm.power_0x02`, `…fpga_0x000c`. In the
GDS you see what the device holds next to what flight software made of it. A value of up to
four bytes, or a signal, is one channel of that width; `telemetry: false` on a model leaves its
registers out. They are published with `wstsControl`'s telemetry, once a second.

## Start one with `new-model`

For a device with behaviour of its own, start a model of the project:

```bash
fprime-wsts new-model Thermometer --kind i2c --endpoint thermoBus --address 0x48 --project my-mission-sim
```

It writes `Models/Thermometer.hpp` and `.cpp` (and the `Models` module and `library.cmake` if
the project has none), and names the model in the manifest (`--manifest`, or the project's
one deployment manifest), keeping its comments:

```yaml
models:
  - {name: thermometer, class: Sim::Thermometer, header: Models/Thermometer.hpp, endpoint: thermoBus, kind: i2c, address: 0x48}
cmake:
  depends: [Models]
```

The model builds and answers from the first run. Each kind starts from a common form of its
device:

| `--kind` | Starts as |
|---|---|
| `i2c` | A register map with an auto-incrementing pointer |
| `spi` | A register map, bit 7 of the first byte for a read |
| `bytestream` | A device that answers lines of text (`*IDN?`, `READ?`), and reports every `period` ticks if asked |
| `gpio` | A device that raises a `ready` pin `delay` ticks after its own pin goes high |
| `register` | A `Wsts::RegisterFile` with identity, control, status and data registers, and a measurement that ends with an interrupt |

Each has a `value` parameter in engineering units that takes a number or a signal, so its
readings move before you write anything (`model_set model=thermometer key=value
value="sine(offset=25, amplitude=5, period=60)"`). The TODOs mark where the device's own
registers, scale and commands go. `--python` writes `pymodels/<name>.py` instead, a Python
model that starts with the run (below).

`fprime-wsts init --models` does the same for every bus endpoint of a new simulation
project, so a fresh project builds with every endpoint answering.

To find which devices still need a model, run the deployment and look where nothing
answered: `fprime-wsts status <dir> --bus` lists each endpoint's traffic and the I2C
addresses and register offsets flight software reached with nothing there, and the GDS shows
`wstsControl.UnansweredTraffic` the first time each one happens.

## A C++ model

Implement one of the device interfaces in `Wsts/Core/Bus.hpp` (`ByteStreamDevice`,
`I2cDevice`, `SpiDevice`, `GpioDevice`, `RegisterDevice`). Derive from `Wsts::Model` as
well to take parameters or act on every tick:

```cpp title="my-mission-sim/Models/Thermometer.hpp"
#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>

class Thermometer : public Wsts::Model, public Wsts::I2cDevice {
  public:
    // Return 0, or a Drv::I2cStatus value. `read` arrives sized to the requested length.
    int transact(std::uint32_t addr, const Wsts::Bytes& write, Wsts::Bytes& read) override {
        for (auto& byte : read) byte = m_raw;
        return 0;
    }
    // Reached by the manifest's `params`, by model_set and by the MODEL_SET command
    bool set(const std::string& key, const std::string& value) override {
        if (key != "raw") return false;
        m_raw = static_cast<std::uint8_t>(std::stoi(value, nullptr, 0));
        return true;
    }
    void onTick(std::uint64_t tick) override {}  // once per simulation tick
  private:
    std::uint8_t m_raw = 0;
};
```

Put it in a module that the simulation project builds (`library.cmake` adds `Models`),
and name it in the manifest:

```yaml
models:
  - {name: thermo, class: Thermometer, header: Models/Thermometer.hpp,
     endpoint: thermoBus, kind: i2c, address: 0x48}
cmake:
  depends: [Models]       # so the generated set-up can create it
```

The example's `Sim::ImuModel` (`examples/mission-sim/Models`) is a fuller one: an IMU whose
acceleration is set in g rather than in register bytes, so a test can write
`model_set("imu", "accel_x", -0.5)`.

!!! warning "Models are called from several threads"

    Flight threads (inside the port call), the tick thread (`onTick`) and bridge threads
    (`set`) all reach a model. Guard shared state, and do not block for long in a model:
    the flight thread that made the port call is waiting.

Notes for byte streams: a byte stream model replies through the `ByteSink` it is handed,
or at any time with `Wsts::Bus::get().byteStreamInject(endpoint, bytes)`, and is told its
endpoint through `set("endpoint", …)` at start-up.

## A model in Python

A test can become the device itself, through the bridge:

```python
from fprime_wsts.bridge import WstsBridge

sim = WstsBridge("tcp:50100")
sim.serve_i2c("thermoBus", 0x48, lambda write, length: (0, bytes([42] * length)))
```

The handler runs for each transaction and returns `(status, data)`. If it does not answer
within two seconds, flight software gets an error status. When the client disconnects
its device disappears from the bus (I2C then reports an address error at that address);
other devices on the endpoint, at other addresses, stay. `serve_spi` works
the same way, and `serve_register` serves a [register block](registers.md#a-register-block-in-python).

A byte stream device answers what flight software sends, and a GPIO model follows an
output pin. Their handlers may call the bridge:

```python
sim.serve_bytestream("gpsUart", lambda data: b"$GPGGA,...\r\n" if data.startswith(b"$PQ") else None)
sim.serve_gpio("heaterPin", lambda level: sim.gpio_force("heaterOnSense", level))
```

In a test you can also do it by hand: `subscribe()`, `wait_for(...)` a `bs_tx` record, then
`inject_text(...)`.

### Python models that start with the run

A model that should always be there belongs to the simulation project, not to one test.
Put it in a file that defines `serve(sim)`, and name the file in the manifest:

```python title="my-mission-sim/models/thermo.py"
def serve(sim):
    raw = {"value": 42}
    sim.serve_i2c("thermoBus", 0x48, lambda write, length: (0, bytes([raw["value"]] * length)))
```

```yaml
run:
  models: [models/thermo.py]      # relative to the manifest
```

`fprime-wsts run` (and the pytest plugin) then starts the deployment paused, starts one
Python process that calls each file's `serve` with a connected `WstsBridge`, and resumes
when all have returned: the models are on their endpoints before flight software's first
tick. Their output is in `wsts-logs/models.log`, and a file that fails stops the run with
its error. They need the manifest's `bridge`.

A Python model costs a round trip per transaction and answers within two seconds or not
at all, and it is not there for what flight software does before the first tick: a device
that topology set-up code reads (an ID register checked in `configure`) needs a C++ model.

## Device models from a datasheet

To write a model by hand, start from `new-model`; for a model checked against the real part,
use the agent skill. The [`wsts-device-model` agent skill](agents.md#wsts-device-model) writes a model from a
datasheet, ICD or protocol spec, and checks it against the real device: it probes a bench
unit over its serial, I2C, SPI or GPIO interface, captures the answers, and replays the
captures against the model.

## Next

[Register blocks and interrupts](registers.md), for FPGA blocks and SoC peripherals.
