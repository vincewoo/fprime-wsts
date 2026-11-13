# Device model patterns

## Contents

1. Anatomy common to every model
2. Byte stream: ASCII command/response with streaming output and reply latency
3. Byte stream: binary frames with a checksum
4. I2C: register map with multi-byte pointer and read side effects
5. SPI: command now, answer in the next transfer
6. GPIO: a data-ready output and a reset input
7. Register block (FPGA / SoC peripheral)
8. As an F´ simulator component
9. As a Python prototype over the bridge
10. Manifest and build

Every skeleton here compiles against `Wsts/Core` and runs under `conform.py`. Copy one,
rename it, and replace the protocol.

## 1. Anatomy

```cpp
#include <Wsts/Core/Bus.hpp>       // the device interfaces, Bus
#include <Wsts/Core/Control.hpp>   // Wsts::Model: set() and onTick()
#include <Wsts/Core/Json.hpp>      // Wsts::parseInt, fromHex, toHex (handy for set())

namespace Sim {
class XModel : public Wsts::Model, public Wsts::ByteStreamDevice { ... };
}
```

- `set(key, value)` is reached by the manifest's `params` at start-up, by `model_set` from
  the bridge and scenarios, and by the `MODEL_SET` command. `"endpoint"` is always set
  first. Return `false` for an unknown key or a bad value: the generated set-up reports
  it, and `model_set` fails visibly.
- `onTick(tick)` runs once per simulation tick, on the tick thread. It stops while the
  simulation is paused and scales with `set_rate`. Measure time in ticks, never with the
  wall clock: then pause, rate and replay all work.
- Guard members with one mutex. Copy what you need to send out of the lock, then send.
  Bus calls go through faults and observers and may call back into flight code.
- Expose physical quantities (and, if useful, failure modes such as `fail_mode=nack`) as
  parameters, and document them in a `//!` block over the class, as `ImuModel.hpp` does.
  Tests drive the model through them.

## 2. Byte stream: ASCII command/response, streaming, latency

```cpp
// Models/TempSensorModel.hpp
#pragma once
#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Json.hpp>

#include <cstdio>
#include <cstdlib>
#include <deque>
#include <mutex>
#include <string>

namespace Sim {

//! ACME TS-200 serial thermometer. ASCII, CR LF terminated.
//!   "T?"      -> "T=<deg C, 1 decimal>"     [DS §6.2]
//!   "RATE n"  -> "OK"   streams "T=..." every n seconds, 0 stops   [DS §6.4]
//!   other     -> "ERR"                       [HW bogus.jsonl:3]
//! Parameters: temp_c (default 21.0), latency_ticks (default 0), tick_hz (default 10)
class TempSensorModel : public Wsts::Model, public Wsts::ByteStreamDevice {
  public:
    bool set(const std::string& key, const std::string& value) override {
        std::lock_guard<std::mutex> lock(m_mutex);
        char* end = nullptr;
        if (key == "endpoint") { m_endpoint = value; return true; }
        if (key == "temp_c") {
            const double v = std::strtod(value.c_str(), &end);
            if (end == value.c_str() || *end != '\0' || v < -40.0 || v > 125.0) return false;  // sensor range
            m_tempC = v;
            return true;
        }
        std::int64_t n = 0;
        if (!Wsts::parseInt(value, n) || n < 0) return false;
        if (key == "latency_ticks") { m_latency = static_cast<std::uint64_t>(n); return true; }
        if (key == "tick_hz") { m_tickHz = n > 0 ? static_cast<std::uint64_t>(n) : 1; return true; }
        return false;
    }

    void onBytes(const Wsts::Bytes& tx, Wsts::ByteSink& reply) override {
        std::string now;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_rx.append(tx.begin(), tx.end());                 // flight may send a line in pieces
            std::size_t eol;
            while ((eol = m_rx.find("\r\n")) != std::string::npos) {
                const std::string line = m_rx.substr(0, eol);
                m_rx.erase(0, eol + 2);
                const std::string answer = this->handle(line);
                if (m_latency == 0) {
                    now += answer;
                } else {
                    m_pending.push_back({m_tick + m_latency, answer});
                }
            }
        }
        if (!now.empty()) {
            reply.send(Wsts::Bytes(now.begin(), now.end()));   // outside the lock
        }
    }

    void onTick(std::uint64_t tick) override {
        std::string out;
        std::string endpoint;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_tick = tick;
            while (!m_pending.empty() && m_pending.front().due <= tick) {
                out += m_pending.front().text;
                m_pending.pop_front();
            }
            if (m_ratePeriod != 0 && tick % (m_ratePeriod * m_tickHz) == 0) {
                out += this->reading();
            }
            endpoint = m_endpoint;
        }
        if (!out.empty() && !endpoint.empty()) {
            Wsts::Bus::get().byteStreamInject(endpoint, Wsts::Bytes(out.begin(), out.end()));
        }
    }

  private:
    struct Pending { std::uint64_t due; std::string text; };

    std::string reading() const {                                // called with the lock held
        char buf[32];
        std::snprintf(buf, sizeof buf, "T=%.1f\r\n", m_tempC);
        return buf;
    }
    std::string handle(const std::string& line) {               // called with the lock held
        if (line == "T?") return this->reading();
        if (line.rfind("RATE ", 0) == 0) {
            std::int64_t n = 0;
            if (Wsts::parseInt(line.substr(5), n) && n >= 0 && n <= 60) {
                m_ratePeriod = static_cast<std::uint64_t>(n);
                return "OK\r\n";
            }
        }
        return "ERR\r\n";
    }

    std::mutex m_mutex;
    std::string m_endpoint;
    std::string m_rx;
    std::deque<Pending> m_pending;
    double m_tempC = 21.0;
    std::uint64_t m_latency = 0;
    std::uint64_t m_tickHz = 10;
    std::uint64_t m_ratePeriod = 0;
    std::uint64_t m_tick = 0;
};

}  // namespace Sim
```

Notes:

- Reply latency comes from the captures (`latency_ms`). Model it only if flight software
  is sensitive to it (a timeout close to it, a state machine that sends again). At 10 Hz a
  tick is 100 ms. A latency below a tick is best modelled as 0.
- A device that streams on its own clock needs to know ticks per second: take
  `tick_hz` as a parameter that the manifest sets to match the element's tick.
- An echoing device: send the echo first, from `onBytes`.

## 3. Byte stream: binary frames with a checksum

```cpp
// Frame: A5 5A | len (1) | cmd (1) | payload (len-1) | crc16 (CCITT-FALSE, big-endian, over len..payload)
namespace Sim {
inline std::uint16_t crc16CcittFalse(const std::uint8_t* p, std::size_t n) {
    std::uint16_t crc = 0xFFFF;
    while (n--) {
        crc ^= static_cast<std::uint16_t>(*p++) << 8;
        for (int i = 0; i < 8; i++) crc = (crc & 0x8000) ? static_cast<std::uint16_t>((crc << 1) ^ 0x1021) : static_cast<std::uint16_t>(crc << 1);
    }
    return crc;
}

class FramedModel : public Wsts::Model, public Wsts::ByteStreamDevice {
  public:
    void onBytes(const Wsts::Bytes& tx, Wsts::ByteSink& reply) override {
        Wsts::Bytes out;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_rx.insert(m_rx.end(), tx.begin(), tx.end());
            for (;;) {
                // Resynchronise on the sync word, as the device does: drop bytes before it
                std::size_t s = 0;
                while (s + 1 < m_rx.size() && !(m_rx[s] == 0xA5 && m_rx[s + 1] == 0x5A)) s++;
                m_rx.erase(m_rx.begin(), m_rx.begin() + static_cast<long>(s));
                if (m_rx.size() < 3) break;
                const std::size_t len = m_rx[2];
                const std::size_t total = 2 + 1 + len + 2;
                if (m_rx.size() < total) break;                       // wait for the rest
                const std::uint16_t want = crc16CcittFalse(&m_rx[2], 1 + len);
                const std::uint16_t got = static_cast<std::uint16_t>(m_rx[total - 2] << 8 | m_rx[total - 1]);
                Wsts::Bytes frame(m_rx.begin(), m_rx.begin() + static_cast<long>(total));
                m_rx.erase(m_rx.begin(), m_rx.begin() + static_cast<long>(total));
                const Wsts::Bytes answer = want == got ? this->handle(frame[3], &frame[4], len - 1)
                                                       : this->nak(0x01);   // checksum error [DS §5.3]
                out.insert(out.end(), answer.begin(), answer.end());
            }
        }
        if (!out.empty()) reply.send(out);
    }

  private:
    Wsts::Bytes encode(std::uint8_t cmd, const Wsts::Bytes& payload) const {
        Wsts::Bytes f{0xA5, 0x5A, static_cast<std::uint8_t>(payload.size() + 1), cmd};
        f.insert(f.end(), payload.begin(), payload.end());
        const std::uint16_t crc = crc16CcittFalse(&f[2], f.size() - 2);
        f.push_back(static_cast<std::uint8_t>(crc >> 8));
        f.push_back(static_cast<std::uint8_t>(crc & 0xFF));
        return f;
    }
    Wsts::Bytes nak(std::uint8_t code) const { return this->encode(0x7F, {code}); }
    Wsts::Bytes handle(std::uint8_t cmd, const std::uint8_t* payload, std::size_t n) {
        (void)payload; (void)n;
        switch (cmd) {
            case 0x01: return this->encode(0x81, {0x02, 0x01});        // version 2.1 [HW id.jsonl:3]
            default: return this->nak(0x02);                          // unknown command
        }
    }

    std::mutex m_mutex;
    Wsts::Bytes m_rx;
};
}  // namespace Sim
```

Confirm the checksum algorithm and byte order against a captured frame before writing
the model. Multi-byte fields: write explicit shift code for the device's byte order. Never
`memcpy` a struct, because the host's layout and endianness are not the target's.

## 4. I2C: register map, multi-byte pointer, read side effects

`Wsts::RegisterMapI2c` (stock) covers a plain 8-bit-pointer, auto-incrementing map set from
`params`. Write your own when the device has behaviour:

```cpp
namespace Sim {
//! Parameters: pressure_pa, temp_c, nack (1: do not acknowledge, as when asleep)
class BaroModel : public Wsts::Model, public Wsts::I2cDevice {
  public:
    static constexpr std::uint8_t REG_STATUS = 0x07, REG_DATA = 0x08, REG_CTRL = 0x26, REG_ID = 0x0C;

    BaroModel() { m_regs[REG_ID] = 0xC4; }

    int transact(std::uint32_t addr, const Wsts::Bytes& write, Wsts::Bytes& read) override {
        (void)addr;
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_nack) return 1;                                   // Drv.I2cStatus address error
        if (!write.empty()) {
            m_ptr = write[0];
            for (std::size_t i = 1; i < write.size(); i++) this->store(m_ptr++, write[i]);
        }
        for (auto& b : read) {
            b = m_regs[m_ptr];
            if (m_ptr == REG_DATA + 4) m_regs[REG_STATUS] &= static_cast<std::uint8_t>(~0x08);  // reading the last data byte clears DRDY
            m_ptr = m_ptr == REG_DATA + 4 ? REG_DATA : static_cast<std::uint8_t>(m_ptr + 1);  // the pointer wraps in the data block [DS 7.2]
        }
        return 0;
    }
    bool pokeRegister(std::uint8_t reg, std::uint8_t value) override {   // i2c_reg_set action
        std::lock_guard<std::mutex> lock(m_mutex);
        m_regs[reg] = value;
        return true;
    }
    bool set(const std::string& key, const std::string& value) override {
        char* end = nullptr;
        const double v = std::strtod(value.c_str(), &end);
        if (end == value.c_str() || *end != '\0') return false;
        std::lock_guard<std::mutex> lock(m_mutex);
        if (key == "pressure_pa" && v >= 20000 && v <= 110000) { m_pressurePa = v; return true; }
        if (key == "temp_c" && v >= -40 && v <= 85) { m_tempC = v; return true; }
        if (key == "nack") { m_nack = v != 0; return true; }
        return false;
    }
    void onTick(std::uint64_t tick) override {               // the conversion finishes a tick after it starts
        std::lock_guard<std::mutex> lock(m_mutex);
        m_tick = tick;
        if (m_convertAt != 0 && tick >= m_convertAt) {
            m_convertAt = 0;
            const auto p = static_cast<std::uint32_t>(m_pressurePa * 64.0);          // Q18.6 pascals, 20 bits used [DS 7.4]
            const auto t = static_cast<std::int16_t>(m_tempC * 256.0);              // Q8.8 degrees C
            m_regs[REG_DATA] = static_cast<std::uint8_t>(p >> 16);
            m_regs[REG_DATA + 1] = static_cast<std::uint8_t>(p >> 8);
            m_regs[REG_DATA + 2] = static_cast<std::uint8_t>(p);
            m_regs[REG_DATA + 3] = static_cast<std::uint8_t>(static_cast<std::uint16_t>(t) >> 8);
            m_regs[REG_DATA + 4] = static_cast<std::uint8_t>(t);
            m_regs[REG_STATUS] |= 0x08;                                             // DRDY
        }
    }
    void reset() {                                           // power-on state, for a reset pin
        std::lock_guard<std::mutex> lock(m_mutex);
        std::fill(std::begin(m_regs), std::end(m_regs), 0);
        m_regs[REG_ID] = 0xC4;
        m_ptr = 0;
        m_convertAt = 0;
    }

  private:
    void store(std::uint8_t reg, std::uint8_t value) {
        if (reg == REG_ID || (reg >= REG_DATA && reg <= REG_DATA + 4)) return;   // read-only
        m_regs[reg] = value;
        if (reg == REG_CTRL && (value & 0x02)) m_convertAt = m_tick + 1;         // one-shot conversion
    }
    std::mutex m_mutex;
    std::uint8_t m_regs[256]{};
    std::uint8_t m_ptr = 0;
    bool m_nack = false;
    double m_pressurePa = 101325.0, m_tempC = 20.0;
    std::uint64_t m_tick = 0, m_convertAt = 0;
};
}  // namespace Sim
```

- A 16-bit pointer (EEPROMs, some sensors): take `write[0] << 8 | write[1]` as the pointer.
- One model can serve several addresses only by being attached several times. Prefer
  one model per address.
- Return codes: `0`, or a `Drv.I2cStatus` value (1 address/NACK, 2 write, 3 read, 5 other),
  which is what flight sees.

## 5. SPI: command now, answer in the next transfer

```cpp
namespace Sim {
class AdcModel : public Wsts::Model, public Wsts::SpiDevice {
  public:
    int transfer(const Wsts::Bytes& write, Wsts::Bytes& read) override {
        std::lock_guard<std::mutex> lock(m_mutex);
        for (std::size_t i = 0; i < read.size(); i++) read[i] = i < m_next.size() ? m_next[i] : 0xFF;  // MISO idles high
        m_next.clear();
        if (!write.empty() && (write[0] & 0xF8) == 0x08) {      // "convert channel n" [DS 8.1]
            const unsigned ch = write[0] & 0x07;
            const std::uint16_t code = static_cast<std::uint16_t>(m_volts[ch] / 2.5 * 4095.0 + 0.5);
            m_next = {static_cast<std::uint8_t>(code >> 8), static_cast<std::uint8_t>(code & 0xFF)};
        }
        return 0;
    }
  private:
    std::mutex m_mutex;
    Wsts::Bytes m_next;
    double m_volts[8]{};
};
}  // namespace Sim
```

When the device answers *within* the same transfer (the command in byte 0, data clocked
out in bytes 1..n), fill `read[1..]` from the command in `write[0]` instead.

## 6. GPIO

The device **drives** a pin flight reads (data ready, fault, busy): no `GpioDevice` is
needed. The device model calls `Wsts::Bus::get().gpioSetInput("<pin endpoint>", level)`, and
the endpoint name is a parameter (`drdy_endpoint`). The surrogate turns the change into
the flight interrupt.

Flight **drives** a pin the device reacts to (reset, enable, chip power): attach a
`GpioDevice` on that endpoint. To reach the main model, give the pin model a pointer to
it from the manifest's `setup:` code (`model_<name>` is each model's variable):

```cpp
class ResetPin : public Wsts::Model, public Wsts::GpioDevice {
  public:
    BaroModel* device = nullptr;
    void onWrite(bool level) override {
        if (device && !level) device->reset();    // active-low reset [DS 4.1]
    }
};
```

```yaml
models:
  - {name: baro, class: Sim::BaroModel, header: Models/BaroModel.hpp, endpoint: i2cBus, kind: i2c, address: 0x60}
  - {name: baroReset, class: Sim::ResetPin, header: Models/BaroModel.hpp, endpoint: baroResetPin, kind: gpio}
setup: |
  model_baroReset.device = &model_baro;
```

## 7. Register block

Derive from `Wsts::RegisterFile`. The full pattern, with threads, interrupts, widths and
faults, is in `docs/surrogate-guide.md` under "A register block". If you override `set` or
`onTick`, call `RegisterFile::set` / `RegisterFile::onTick` for what you do not handle:
they carry the `reg.*` parameters and the periodic interrupt. Linux register blocks are
rarely probed directly (`/dev/mem`, UIO). Treat any write there as hazardous, and
usually model from the document and flight-software recordings.

## 8. As an F´ simulator component

When the simulator needs its own commands, events, telemetry or parameters, follow
`examples/mission-sim/SensorSim/SerialSensorSim` and the surrogate guide ("Device
simulators as F´ components"). Keep the protocol in a plain class with the same shape as
a model:

```cpp
class TempSensorCore {           // plain C++: onBytes(tx, sink), set(), onTick()
  ...
};
```

The component owns a `TempSensorCore`. Its `drvReceiveIn` handler passes bytes to
`core.onBytes()` with a sink that sends through `drvSendOut`. Its parameters and commands
call `core.set()`. `conform.py` then tests the core directly (`--class Sim::TempSensorCore`,
if it also derives from `Wsts::ByteStreamDevice`). The component adds only F´
plumbing, which the e2e run covers.

## 9. As a Python prototype over the bridge

Useful for working out a protocol against captures before writing C++:

```python
from fprime_wsts.bridge import WstsBridge
sim = WstsBridge("tcp:50101")
regs = {0x0C: 0xC4}
sim.serve_i2c("i2cBus", 0x60, lambda write, n: (0, bytes(regs.get(write[0] + i, 0) for i in range(n))))
```

Byte streams in Python: `sim.subscribe()`, watch `bs_tx` records on the endpoint, and
answer with `sim.inject_bytes(endpoint, data)`. Port to C++ once it behaves. The C++
model is what the deployment and CI use.

## 10. Manifest and build

```yaml
models:
  - name: tempSensor                       # model_set / MODEL_SET name
    class: Sim::TempSensorModel
    header: Models/TempSensorModel.hpp      # relative to the simulation project root
    endpoint: uart                          # the substituted driver instance's endpoint
    kind: bytestream                        # bytestream | i2c | spi | gpio | register
    params: {temp_c: "21.5", tick_hz: "10"}
cmake:
  depends: [Models]                         # the module that builds the model
```

The module: a `CMakeLists.txt` with `register_fprime_library(SOURCES … HEADERS … DEPENDS
Wsts_Core)` (as `examples/mission-sim/Models/CMakeLists.txt`). In a simulation project it
must also be added by `library.cmake`. In the example, that happens through the
`MISSION_SIM_MODULES` cache list in `cmake.cache`. A header-only model still needs to be
in a module that is built.
