# Writing surrogates and device models

There are two things you might write, and most projects need only the second.

- A **surrogate** is an F´ component that takes the place of a flight component in the
  simulated topology. The toolkit ships surrogates for the standard driver interfaces.
  Write one when a project driver has its own port interface, when you want to replace a
  whole device manager, or when you need a flight-identical dictionary (a mirror).
- A **device model** is a plain C++ class that sits on the simulation bus behind a
  surrogate and behaves like the hardware. Models can also live in another process.

## Device models

Implement one of the interfaces in `Sse/Core/Bus.hpp`. Derive from `Sse::Model` as well if
the model takes parameters or needs to act on every tick.

```cpp
#include <Sse/Core/Bus.hpp>
#include <Sse/Core/Control.hpp>

class Thermometer : public Sse::Model, public Sse::I2cDevice {
  public:
    // Return 0, or a Drv::I2cStatus value. `read` arrives sized to the requested length.
    int transact(std::uint32_t addr, const Sse::Bytes& write, Sse::Bytes& read) override {
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

Put it in a module the project builds, and name it in the manifest:

```yaml
models:
  - {name: thermo, class: Thermometer, header: Sim/Thermometer.hpp, endpoint: thermoBus, kind: i2c, address: 0x48}
cmake:
  extra_subdirectories: [Sim]
  depends: [Sim]
```

Notes:

- Model methods are called from flight threads (inside the port call), from the tick thread
  and from bridge threads. Guard shared state.
- A byte stream model replies through the `ByteSink` it is handed, or at any time with
  `Sse::Bus::get().byteStreamInject(endpoint, bytes)`. It is told its endpoint through
  `set("endpoint", …)` at start-up.
- Do not block in a model for long: the flight thread that made the port call is waiting.

### Models in Python

```python
from fprime_sse.bridge import SseBridge

sim = SseBridge("tcp:50100")
sim.serve_i2c("thermoBus", 0x48, lambda write, length: (0, bytes([42] * length)))
```

The handler runs for each transaction; if it does not answer within two seconds flight
software gets an error status. When the client disconnects the device disappears from the
bus (I2C then reports an address error).

## Surrogates

A surrogate is a normal F´ component with three conventions.

1. **Same ports as what it replaces.** For every port the topology connects on the flight
   instance, the surrogate must have a port with the same name, port type and direction.
   Implementing the same FPP interface (`import Drv.I2c`) gives you that. The generator
   checks it and reports the ports that do not match.
2. **A binding hook**, called by generated code during `configComponents`:

   ```cpp
   void sseBind(const char* endpoint, const char* options = "");
   ```

3. **The methods flight topology code calls** on the flight class, if that code is to
   compile unchanged. The generator aliases the flight class name to the surrogate, so
   `uart.open("/dev/ttyS1", Drv::PosixUartDriver::BAUD_115K, …)` resolves against your
   class: provide `open` and the enumerators. A variadic template is enough when the
   arguments do not matter:

   ```cpp
   template <typename... Args> bool open(Args&&...) { return true; }
   ```

   Flight code can also use `#ifdef SSE_SIM`, which is defined in every simulated build.

Port handlers forward to the bus:

```cpp
Drv::I2cStatus SimI2c::writeRead_handler(FwIndexType, U32 addr, Fw::Buffer& w, Fw::Buffer& r) {
    Sse::Bytes write(w.getData(), w.getData() + w.getSize());
    Sse::Bytes read(r.getSize(), 0);
    const int status = Sse::Bus::get().i2cTransact(m_endpoint, addr, write, read);
    std::memcpy(r.getData(), read.data(), read.size());
    return static_cast<Drv::I2cStatus::T>(status);
}
```

`Sse/Drv/SimByteStream` is the fullest example (receive path, buffer ownership, the ready
signal). Going through the bus is what gives a surrogate fault injection, recording and
bridge access for free. A surrogate for a device manager may skip the bus and produce its
outputs directly; it then has none of those.

Register the surrogate in a catalog so it is chosen automatically:

```yaml
# sim/sse-catalog.yaml, named under `catalogs:` in the manifest
surrogates:
  - surrogate: Proj.SimFpga
    replaces: [Proj.FpgaDriver]
```

A surrogate may declare its own commands, events and telemetry. They appear in the
simulated dictionary under the replaced instance's base ID, and the report lists the
difference from flight.

### Mirror surrogates

```bash
fprime-sse scaffold VxWorksDrv.VxWorksUartDriver --project examples/vx-demo --output Sim/SimVxUart
```

This copies the component's model, with its included event and telemetry definitions,
under a new name, and writes the build file, a catalog entry and a README with the
remaining steps (`fprime-util impl` for the implementation templates, then the binding
hook and bus calls). The scaffold does not write the C++ implementation.
