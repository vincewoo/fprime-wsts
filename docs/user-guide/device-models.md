# Device Models

A device model is what answers on a bus endpoint: the IMU behind `i2cBus`, the GPS behind
`gpsUart`. Flight software cannot tell a model from the device; the driver above it is a
surrogate, and the manager above that is flight code.

You have four ways to provide one:

| | Where it runs | Best for |
|---|---|---|
| A **stock model** | In the process | Register-addressed I2C devices, periodic serial sensors, loopbacks, register blocks |
| A **C++ model** of your own | In the process | Devices with behaviour: engineering units, state machines, timing |
| A **Python model** over the bridge | In your test process | A device a test wants to control transaction by transaction |
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
(0x68) and power monitor (0x40) do. Change a parameter while running with
`model_set power 0x02 "0x5D 0xC0"` (from Python, a scenario or the `MODEL_SET` command).

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
the device disappears from the bus (I2C then reports an address error). `serve_spi` works
the same way, and `serve_register` serves a [register block](registers.md#a-register-block-in-python).

Byte stream and GPIO devices need no `serve_*` call: subscribe, watch what flight
software sends, and answer:

```python
sim.subscribe()
msg = sim.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "gpsUart")
sim.inject_text("gpsUart", "$GPGGA,...\r\n")
```

## Device models from a datasheet

The [`wsts-device-model` agent skill](agents.md#wsts-device-model) writes a model from a
datasheet, ICD or protocol spec, and checks it against the real device: it probes a bench
unit over its serial, I2C, SPI or GPIO interface, captures the answers, and replays the
captures against the model.

## Next

[Register blocks and interrupts](registers.md), for FPGA blocks and SoC peripherals.
