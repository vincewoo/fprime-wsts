"""`fprime-wsts new-model`: start a device model in a simulation project.

Writes a model that builds and answers from the first run, with TODOs where the device's own
behaviour goes:

* C++ (the default): `Models/<Name>.hpp` and `.cpp`, the `Models` module's `CMakeLists.txt`
  (made, or the files added to it), and `library.cmake` when the project has none.
* Python (`--python`): `pymodels/<name>.py`, which defines `serve(sim)` (`run.models`).

and names it in the manifest (`models:` and `cmake.depends`, or `run.models`), when there is
one manifest to name it in or `--manifest` says which. A manifest is edited as text, so its
comments stay; what cannot be placed safely is printed to paste instead.

Each model has a `value` parameter, in engineering units, that takes a number or a signal
(`sine(offset=25, amplitude=5, period=60)`, Wsts/Core/Signal.hpp), so its readings move
before any behaviour is written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import List, Optional

import yaml

from .fpp import WstsError

KINDS = ("i2c", "spi", "bytestream", "gpio", "register", "packet")
MODULE = "Models"
PY_DIR = "pymodels"  # not "models": beside Models/, the same directory where case does not count
MARKER = "# Written by fprime-wsts new-model: it adds the files of each new model here."

# ----------------------------------------------------------------------
# C++ templates. $Name, $NS, $GUARD, $model, $endpoint
# ----------------------------------------------------------------------

HEADER_START = """\
// ======================================================================
// \\title  $Name.hpp
// \\brief  Device model: $summary
//
// Started by `fprime-wsts new-model`. TODO: the device it models (part, datasheet, ICD).
// ======================================================================
#ifndef $GUARD
#define $GUARD

#include <array>
#include <cstdint>
#include <mutex>
#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Models.hpp>
#include <Wsts/Core/Signal.hpp>

namespace $NS {

"""

HEADER_END = """
}  // namespace $NS

#endif
"""

SOURCE_START = """\
// ======================================================================
// \\title  $Name.cpp
// \\brief  Device model: $summary
// ======================================================================
#include "$Name.hpp"

#include <cmath>

#include <Wsts/Core/Json.hpp>

namespace $NS {

namespace {
//! A number or a signal of simulated time (Wsts/Core/Signal.hpp)
[[maybe_unused]] bool parseSignal(const std::string& text, Wsts::Signal& signal) {
    std::string error;
    return Wsts::Signal::parse(text, signal, error);
}
}  // namespace

"""

SOURCE_END = """
}  // namespace $NS
"""

CPP = {
    "i2c": ("an I2C device with a register map", """\
//! An I2C device with a register map: a write sets the register pointer (its first byte) and
//! stores what follows; a read returns registers from the pointer on, which auto-increments.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET):
//!   value         the measured value, in engineering units: a number or a signal, e.g.
//!                 "sine(offset=25, amplitude=5, period=60) + noise(sd=0.1)"
//!   <register>    "<byte>": a register, directly, e.g. "0x0F" = "0x68"
class $Name : public Wsts::Model, public Wsts::I2cDevice {
  public:
    // TODO: the device's registers and scale, from its datasheet
    static constexpr std::uint8_t REG_VALUE = 0x00;  //!< value, 16 bits, big-endian, signed
    static constexpr std::uint8_t REG_ID = 0x0F;     //!< identity
    static constexpr std::uint8_t ID = 0x00;         //!< what REG_ID reads
    static constexpr double LSB_PER_UNIT = 100.0;    //!< counts per engineering unit

    $Name();

    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;
    int transact(std::uint32_t addr, const Wsts::Bytes& write, Wsts::Bytes& read) override;
    bool pokeRegister(std::uint8_t reg, std::uint8_t value) override;

  private:
    //! Store the value in the registers (with the lock held)
    void store(double value);

    std::mutex m_mutex;
    std::array<std::uint8_t, 256> m_registers{};
    std::uint8_t m_pointer = 0;
    Wsts::Signal m_value;
};
""", """\
$Name::$Name() {
    (void)parseSignal("0", m_value);
    m_registers[REG_ID] = ID;
}

bool $Name::set(const std::string& key, const std::string& value) {
    if (key == "value") {
        Wsts::Signal signal;
        if (!parseSignal(value, signal)) {
            return false;
        }
        const std::uint64_t now = Wsts::Control::get().elapsedNs();
        std::lock_guard<std::mutex> lock(m_mutex);
        m_value = signal;
        this->store(signal.at(now));
        return true;
    }
    std::int64_t reg = 0;
    std::uint64_t byte = 0;
    if (Wsts::parseInt(key, reg) && reg >= 0 && reg <= 0xFF && Wsts::parseUInt(value, byte) && byte <= 0xFF) {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_registers[static_cast<std::size_t>(reg)] = static_cast<std::uint8_t>(byte);
        return true;
    }
    return false;  // the endpoint, which every model is told, and anything unknown
}

void $Name::onTick(std::uint64_t tick) {
    (void)tick;
    const std::uint64_t now = Wsts::Control::get().elapsedNs();
    std::lock_guard<std::mutex> lock(m_mutex);
    this->store(m_value.at(now));
}

int $Name::transact(std::uint32_t addr, const Wsts::Bytes& write, Wsts::Bytes& read) {
    (void)addr;
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!write.empty()) {
        m_pointer = write[0];
        // TODO: registers that flight software may not write, and what a write starts
        for (std::size_t i = 1; i < write.size(); i++) {
            m_registers[m_pointer++] = write[i];
        }
    }
    for (std::uint8_t& byte : read) {
        byte = m_registers[m_pointer++];
    }
    return 0;  // or a Drv::I2cStatus value: 1 is an address error (no acknowledge)
}

bool $Name::pokeRegister(std::uint8_t reg, std::uint8_t value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_registers[reg] = value;
    return true;
}

void $Name::store(double value) {
    double counts = std::round(value * LSB_PER_UNIT);
    counts = (counts < -32768) ? -32768 : (counts > 32767) ? 32767 : counts;
    const auto raw = static_cast<std::uint16_t>(static_cast<std::int16_t>(counts));
    m_registers[REG_VALUE] = static_cast<std::uint8_t>(raw >> 8);
    m_registers[REG_VALUE + 1] = static_cast<std::uint8_t>(raw);
}
"""),
    "spi": ("an SPI device with a register map", """\
//! An SPI device with a register map, in the common form: the first byte of a transfer is the
//! register, with bit 7 set for a read; the bytes after it are written to, or read from, the
//! registers from there on.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET):
//!   value         the measured value, in engineering units: a number or a signal, e.g.
//!                 "ramp(from=0, to=100, duration=60)"
//!   <register>    "<byte>": a register, directly
class $Name : public Wsts::Model, public Wsts::SpiDevice {
  public:
    // TODO: the device's command format, registers and scale, from its datasheet
    static constexpr std::uint8_t READ = 0x80;       //!< bit of the first byte that asks for a read
    static constexpr std::uint8_t REG_VALUE = 0x00;  //!< value, 16 bits, big-endian, unsigned
    static constexpr std::uint8_t REG_ID = 0x0F;     //!< identity
    static constexpr std::uint8_t ID = 0x00;         //!< what REG_ID reads
    static constexpr double LSB_PER_UNIT = 100.0;    //!< counts per engineering unit

    $Name();

    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;
    int transfer(const Wsts::Bytes& write, Wsts::Bytes& read) override;

  private:
    void store(double value);

    std::mutex m_mutex;
    std::array<std::uint8_t, 128> m_registers{};
    Wsts::Signal m_value;
};
""", """\
$Name::$Name() {
    (void)parseSignal("0", m_value);
    m_registers[REG_ID] = ID;
}

bool $Name::set(const std::string& key, const std::string& value) {
    if (key == "value") {
        Wsts::Signal signal;
        if (!parseSignal(value, signal)) {
            return false;
        }
        const std::uint64_t now = Wsts::Control::get().elapsedNs();
        std::lock_guard<std::mutex> lock(m_mutex);
        m_value = signal;
        this->store(signal.at(now));
        return true;
    }
    std::int64_t reg = 0;
    std::uint64_t byte = 0;
    if (Wsts::parseInt(key, reg) && reg >= 0 && reg < static_cast<std::int64_t>(m_registers.size()) &&
        Wsts::parseUInt(value, byte) && byte <= 0xFF) {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_registers[static_cast<std::size_t>(reg)] = static_cast<std::uint8_t>(byte);
        return true;
    }
    return false;
}

void $Name::onTick(std::uint64_t tick) {
    (void)tick;
    const std::uint64_t now = Wsts::Control::get().elapsedNs();
    std::lock_guard<std::mutex> lock(m_mutex);
    this->store(m_value.at(now));
}

int $Name::transfer(const Wsts::Bytes& write, Wsts::Bytes& read) {
    std::lock_guard<std::mutex> lock(m_mutex);
    for (std::uint8_t& byte : read) {
        byte = 0;
    }
    if (write.empty()) {
        return 0;
    }
    const bool reading = (write[0] & READ) != 0;
    const std::size_t first = write[0] & static_cast<std::uint8_t>(~READ);
    const std::size_t count = reading ? read.size() : write.size();
    for (std::size_t i = 1; i < count; i++) {
        const std::size_t reg = (first + i - 1) % m_registers.size();
        if (reading) {
            read[i] = m_registers[reg];
        } else {
            m_registers[reg] = write[i];  // TODO: registers that flight software may not write
        }
    }
    return 0;  // or a Drv::SpiStatus value
}

void $Name::store(double value) {
    double counts = std::round(value * LSB_PER_UNIT);
    counts = (counts < 0) ? 0 : (counts > 65535) ? 65535 : counts;
    const auto raw = static_cast<std::uint16_t>(counts);
    m_registers[REG_VALUE] = static_cast<std::uint8_t>(raw >> 8);
    m_registers[REG_VALUE + 1] = static_cast<std::uint8_t>(raw);
}
"""),
    "bytestream": ("a serial device that answers lines of text", """\
//! A serial device that answers commands, one line of text each, and may also report on its
//! own every `period` ticks, like a free-running sensor.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET):
//!   value     the measured value, in engineering units: a number or a signal, e.g.
//!             "sine(offset=20, amplitude=2, period=120) + noise(sd=0.05)"
//!   period    ticks between unsolicited reports; 0 (the default) for none
class $Name : public Wsts::Model, public Wsts::ByteStreamDevice {
  public:
    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;
    void onBytes(const Wsts::Bytes& tx, Wsts::ByteSink& reply) override;

  private:
    //! The answer to one line from flight software (with the lock held); empty for none
    std::string answer(const std::string& line, double value);
    //! A report of the value, as the device formats it
    static std::string report(double value);

    std::mutex m_mutex;
    std::string m_endpoint;
    std::string m_line;
    std::uint32_t m_period = 0;
    Wsts::Signal m_value;
};
""", """\
bool $Name::set(const std::string& key, const std::string& value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (key == "endpoint") {
        m_endpoint = value;
        return true;
    }
    if (key == "value") {
        return parseSignal(value, m_value);
    }
    if (key == "period") {
        std::uint64_t period = 0;
        if (!Wsts::parseUInt(value, period) || period > 0xFFFFFFFFull) {
            return false;
        }
        m_period = static_cast<std::uint32_t>(period);
        return true;
    }
    return false;
}

void $Name::onTick(std::uint64_t tick) {
    std::string text;
    std::string endpoint;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_period == 0 || m_endpoint.empty() || (tick % m_period) != 0) {
            return;
        }
        text = report(m_value.at(Wsts::Control::get().elapsedNs()));
        endpoint = m_endpoint;
    }
    Wsts::Bus::get().byteStreamInject(endpoint, Wsts::Bytes(text.begin(), text.end()));
}

void $Name::onBytes(const Wsts::Bytes& tx, Wsts::ByteSink& reply) {
    std::string out;
    {
        const double value = m_value.at(Wsts::Control::get().elapsedNs());
        std::lock_guard<std::mutex> lock(m_mutex);
        for (const std::uint8_t byte : tx) {
            if (byte == '\\r') {
                continue;
            }
            if (byte != '\\n') {
                m_line.push_back(static_cast<char>(byte));
                continue;
            }
            out += this->answer(m_line, value);
            m_line.clear();
        }
    }
    if (!out.empty()) {
        reply.send(Wsts::Bytes(out.begin(), out.end()));
    }
}

std::string $Name::answer(const std::string& line, double value) {
    // TODO: the device's commands and answers, from its ICD
    if (line == "*IDN?") {
        return "$Name\\r\\n";
    }
    if (line == "READ?") {
        return report(value);
    }
    return "ERR\\r\\n";
}

std::string $Name::report(double value) {
    char text[48];
    std::snprintf(text, sizeof(text), "%.3f\\r\\n", value);
    return text;
}
"""),
    "gpio": ("a device on a GPIO pin", """\
//! A device on a GPIO pin that answers on another: when flight software drives the pin high
//! (an enable, a power switch), the device raises its `ready` line `delay` ticks later, and
//! drops it as soon as the pin goes low.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET):
//!   ready     endpoint of the input pin the device drives; none by default
//!   delay     ticks from the pin going high to `ready` going high (default 10; 0: at once)
class $Name : public Wsts::Model, public Wsts::GpioDevice {
  public:
    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;
    void onWrite(bool level) override;

  private:
    std::mutex m_mutex;
    std::string m_ready;
    std::uint64_t m_delay = 10;
    bool m_level = false;
    std::uint64_t m_countdown = 0;  //!< ticks until `ready` rises; 0 when nothing is pending
};
""", """\
bool $Name::set(const std::string& key, const std::string& value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (key == "ready") {
        m_ready = value;
        return true;
    }
    if (key == "delay") {
        return Wsts::parseUInt(value, m_delay);
    }
    return false;
}

void $Name::onWrite(bool level) {
    std::string ready;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (level == m_level) {
            return;
        }
        m_level = level;
        // TODO: what the device does when its pin changes
        m_countdown = level ? m_delay : 0;
        // Low: `ready` drops at once. High with no delay: it rises at once.
        ready = (!level || m_delay == 0) ? m_ready : "";
    }
    if (!ready.empty()) {
        Wsts::Bus::get().gpioSetInput(ready, level);
    }
}

void $Name::onTick(std::uint64_t tick) {
    (void)tick;
    std::string ready;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_countdown == 0 || --m_countdown != 0) {
            return;
        }
        ready = m_ready;
    }
    if (!ready.empty()) {
        Wsts::Bus::get().gpioSetInput(ready, true);
    }
}
"""),
    "register": ("a block of memory-mapped registers", """\
//! A block of memory-mapped registers (an FPGA block, an SoC peripheral), on the toolkit's
//! Wsts::RegisterFile: identity, control, status and data registers, and a measurement that
//! flight software starts and that ends with an interrupt.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET): those of Wsts::RegisterFile
//! (reg.<offset>, mask.<offset>, ...), and
//!   value     the measurement, in engineering units: a number or a signal
//!   busy      ticks a measurement takes (default 5; at least 1)
class $Name : public Wsts::RegisterFile {
  public:
    // TODO: the block's register map, from its ICD
    static constexpr std::uint64_t REG_ID = 0x00;       //!< identity, read only
    static constexpr std::uint64_t REG_CONTROL = 0x04;  //!< bit 0: start a measurement
    static constexpr std::uint64_t REG_STATUS = 0x08;   //!< bit 0: busy; bit 1: done (write 1 to clear)
    static constexpr std::uint64_t REG_DATA = 0x0C;     //!< the last measurement, in counts
    static constexpr std::uint32_t ID = 0x00000000;
    static constexpr std::uint32_t START = 1;
    static constexpr std::uint32_t BUSY = 1;
    static constexpr std::uint32_t DONE = 2;
    static constexpr std::uint32_t IRQ = 0;             //!< interrupt raised when a measurement ends
    static constexpr double LSB_PER_UNIT = 1000.0;

    $Name();

    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;

  private:
    std::mutex m_stateMutex;
    Wsts::Signal m_value;
    std::uint64_t m_busyTicks = 5;
    std::uint64_t m_countdown = 0;
};
""", """\
$Name::$Name() {
    (void)parseSignal("0", m_value);
    this->define(REG_ID, ID, 0);
    this->define(REG_CONTROL, 0);
    this->define(REG_STATUS, 0, DONE, WRITE_1_TO_CLEAR);
    this->define(REG_DATA, 0, 0);
    this->onWrite(REG_CONTROL, [this](std::uint64_t, std::uint32_t value) {
        if ((value & START) == 0) {
            return;
        }
        {
            std::lock_guard<std::mutex> lock(m_stateMutex);
            m_countdown = (m_busyTicks == 0) ? 1 : m_busyTicks;
        }
        this->setBits(REG_STATUS, BUSY);
    });
}

bool $Name::set(const std::string& key, const std::string& value) {
    if (key == "value") {
        std::lock_guard<std::mutex> lock(m_stateMutex);
        return parseSignal(value, m_value);
    }
    if (key == "busy") {
        std::lock_guard<std::mutex> lock(m_stateMutex);
        return Wsts::parseUInt(value, m_busyTicks);
    }
    return Wsts::RegisterFile::set(key, value);
}

void $Name::onTick(std::uint64_t tick) {
    Wsts::RegisterFile::onTick(tick);
    double value = 0;
    {
        std::lock_guard<std::mutex> lock(m_stateMutex);
        if (m_countdown == 0 || --m_countdown != 0) {
            return;
        }
        value = m_value.at(Wsts::Control::get().elapsedNs());
    }
    // The measurement ends: data, status, interrupt
    (void)this->poke(REG_DATA, static_cast<std::uint32_t>(std::lround(value * LSB_PER_UNIT)));
    (void)this->poke(REG_STATUS, (this->peek(REG_STATUS) & ~BUSY) | DONE);
    this->raise(IRQ);
}
"""),
    "packet": ("an instrument on SpaceWire, reached over RMAP", """\
//! An instrument on a SpaceWire network, on the toolkit's RMAP target (Wsts::RmapTarget):
//! flight software reads and writes its memory with RMAP commands. Its memory holds an
//! identity word and a measurement that follows a signal of simulated time.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET): those of Wsts::RmapTarget (key, base,
//! size, mem.<address>), and
//!   value     the measurement, in engineering units: a number or a signal
class $Name : public Wsts::RmapTarget {
  public:
    // TODO: the instrument's memory map, from its ICD (addresses, sizes, byte order)
    static constexpr std::uint64_t ADDR_ID = 0x0000;     //!< identity, 4 bytes
    static constexpr std::uint64_t ADDR_VALUE = 0x0010;  //!< the measurement, 4 bytes, in counts
    static constexpr std::uint32_t ID = 0x00000000;
    static constexpr double LSB_PER_UNIT = 1000.0;

    $Name();

    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;

  private:
    //! Store a 32-bit word, big-endian as SpaceWire memory is, at an address
    void store(std::uint64_t address, std::uint32_t word);

    std::mutex m_stateMutex;
    Wsts::Signal m_value;
};
""", """\
$Name::$Name() {
    (void)parseSignal("0", m_value);
    this->store(ADDR_ID, ID);
    this->store(ADDR_VALUE, 0);
}

void $Name::store(std::uint64_t address, std::uint32_t word) {
    char text[64];
    std::snprintf(text, sizeof text, "0x%02x 0x%02x 0x%02x 0x%02x", (word >> 24) & 0xFFU, (word >> 16) & 0xFFU,
                  (word >> 8) & 0xFFU, word & 0xFFU);
    (void)Wsts::RmapTarget::set("mem." + std::to_string(address), text);
}

bool $Name::set(const std::string& key, const std::string& value) {
    if (key == "value") {
        std::lock_guard<std::mutex> lock(m_stateMutex);
        return parseSignal(value, m_value);
    }
    return Wsts::RmapTarget::set(key, value);
}

void $Name::onTick(std::uint64_t tick) {
    Wsts::RmapTarget::onTick(tick);
    double value = 0;
    {
        std::lock_guard<std::mutex> lock(m_stateMutex);
        value = m_value.at(Wsts::Control::get().elapsedNs());
    }
    this->store(ADDR_VALUE, static_cast<std::uint32_t>(std::lround(value * LSB_PER_UNIT)));
}
"""),
}

# ----------------------------------------------------------------------
# Python templates
# ----------------------------------------------------------------------

PY_START = '''\
"""Device model: $summary (started by `fprime-wsts new-model`).

TODO: the device it models (part, datasheet, ICD).

`fprime-wsts run` starts it with the deployment (`run.models` in the manifest): `serve(sim)`
registers the handlers, which answer for as long as the deployment runs. Change VALUE while
it runs, or give it another signal (fprime_wsts.signals): it is in engineering units.
"""

import time

from fprime_wsts.signals import signal

ENDPOINT = "$endpoint"
VALUE = signal("sine(offset=25, amplitude=5, period=60)")
START = time.monotonic()


def now() -> float:
    """Seconds since the model started. The bridge's handlers cannot ask the simulation for its
    time, so this is the host's clock: in stepped time, give VALUE a constant instead."""
    return time.monotonic() - START

'''

PY = {
    "i2c": ("an I2C device with a register map", '''\
ADDRESS = $address
REG_VALUE = 0x00  # TODO: the device's registers and scale, from its datasheet
REG_ID = 0x0F
LSB_PER_UNIT = 100.0


def serve(sim):
    registers = bytearray(256)
    registers[REG_ID] = 0x00
    pointer = [0]

    def transact(write: bytes, length: int):
        counts = max(-32768, min(32767, round(VALUE.at(now()) * LSB_PER_UNIT)))
        registers[REG_VALUE:REG_VALUE + 2] = (counts & 0xFFFF).to_bytes(2, "big")
        if write:
            pointer[0] = write[0]
            for byte in write[1:]:  # TODO: registers that flight software may not write
                registers[pointer[0]] = byte
                pointer[0] = (pointer[0] + 1) & 0xFF
        data = bytearray()
        for _ in range(length):
            data.append(registers[pointer[0]])
            pointer[0] = (pointer[0] + 1) & 0xFF
        return 0, bytes(data)  # status 0: success; 1: address error

    sim.serve_i2c(ENDPOINT, ADDRESS, transact)
'''),
    "spi": ("an SPI device with a register map", '''\
READ = 0x80  # TODO: the device's command format, registers and scale
REG_VALUE = 0x00
LSB_PER_UNIT = 100.0


def serve(sim):
    registers = bytearray(128)

    def transfer(write: bytes, length: int):
        counts = max(0, min(65535, round(VALUE.at(now()) * LSB_PER_UNIT)))
        registers[REG_VALUE:REG_VALUE + 2] = counts.to_bytes(2, "big")
        data = bytearray(length)
        if write:
            first = write[0] & ~READ & 0x7F
            if write[0] & READ:
                for i in range(1, length):
                    data[i] = registers[(first + i - 1) % len(registers)]
            else:
                for i, byte in enumerate(write[1:]):
                    registers[(first + i) % len(registers)] = byte
        return 0, bytes(data)

    sim.serve_spi(ENDPOINT, transfer)
'''),
    "bytestream": ("a serial device that answers lines of text", '''\
def answer(line: str):
    """The answer to one line from flight software, or None. TODO: the device's commands"""
    if line == "*IDN?":
        return "$Name\\r\\n"
    if line == "READ?":
        return f"{VALUE.at(now()):.3f}\\r\\n"
    return "ERR\\r\\n"


def serve(sim):
    pending = bytearray()

    def on_bytes(data: bytes):
        pending.extend(data.replace(b"\\r", b""))
        out = ""
        while b"\\n" in pending:
            line, _, rest = bytes(pending).partition(b"\\n")
            pending[:] = rest
            out += answer(line.decode(errors="replace")) or ""
        return out.encode() or None

    sim.serve_bytestream(ENDPOINT, on_bytes)
'''),
    "gpio": ("a device on a GPIO pin", '''\
READY = None  # TODO: endpoint of the input pin the device drives, if any


def serve(sim):
    def on_level(level: bool):
        # TODO: what the device does when flight software drives its pin
        if READY:
            sim.gpio_force(READY, level)

    sim.serve_gpio(ENDPOINT, on_level)
'''),
    "register": ("a block of memory-mapped registers", '''\
REG_ID = 0x00  # TODO: the block's register map, from its ICD
REG_DATA = 0x0C
LSB_PER_UNIT = 1000.0


def serve(sim):
    registers = {REG_ID: 0x00000000, REG_DATA: 0}

    def read(offset: int, width: int):
        if offset == REG_DATA:
            return round(VALUE.at(now()) * LSB_PER_UNIT) & 0xFFFFFFFF
        return registers.get(offset, 0xFFFFFFFF)

    def write(offset: int, width: int, value: int):
        if offset != REG_ID:  # TODO: what a write does
            registers[offset] = value & 0xFFFFFFFF

    sim.serve_register(ENDPOINT, read, write)
'''),
    "packet": ("an instrument on SpaceWire, reached over RMAP", '''\
ADDRESS = $address
KEY = 0x00  # TODO: the instrument's RMAP key, memory map and scale, from its ICD
ADDR_VALUE = 0x0010
LSB_PER_UNIT = 1000.0


def crc(data: bytes) -> int:
    """The RMAP CRC (ECSS-E-ST-50-52C): CRC-8, polynomial x^8 + x^2 + x + 1, bits reflected"""
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xE0 if value & 1 else value >> 1
    return value


def serve(sim):
    memory = bytearray(0x100)

    def on_packet(packet: bytes):
        # An RMAP read command with no reply address: target, protocol, instruction, key,
        # initiator, transaction ID, extended address, address, length, header CRC.
        # TODO: writes and reply addresses, if flight software uses them (Wsts::RmapTarget,
        # in C++, does all of RMAP)
        if len(packet) != 16 or packet[1] != 0x01 or packet[2] & 0xFC != 0x4C or crc(packet[:15]) != packet[15]:
            return None
        address = int.from_bytes(packet[8:12], "big")
        length = int.from_bytes(packet[12:15], "big")
        counts = max(0, round(VALUE.at(now()) * LSB_PER_UNIT)) & 0xFFFFFFFF
        memory[ADDR_VALUE:ADDR_VALUE + 4] = counts.to_bytes(4, "big")
        status = 0x03 if packet[3] != KEY else 0x0A if address + length > len(memory) else 0x00
        data = bytes(memory[address:address + length]) if status == 0 else b""
        header = bytes([packet[4], 0x01, packet[2] & 0x3F, status, packet[0], packet[5], packet[6], 0x00])
        header += len(data).to_bytes(3, "big")
        return header + bytes([crc(header)]) + data + bytes([crc(data)])

    sim.serve_packet(ENDPOINT, ADDRESS, on_packet)
'''),
}

CMAKE_TEMPLATE = """\
# Device models: plain C++ on the WSTS bus
""" + MARKER + """
register_fprime_library(
    SOURCES
    HEADERS
    DEPENDS
        Wsts_Core
)
"""

LIBRARY_CMAKE = """\
####
# library.cmake: the modules of this simulation project (written by fprime-wsts new-model).
# The generated simulated deployment lists this project under library_locations, so F Prime
# includes this file.
####
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Models")
"""


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------


@dataclass
class Written:
    files: List[str] = field(default_factory=list)
    manifest_edits: List[str] = field(default_factory=list)
    todo: List[str] = field(default_factory=list)  # what the user has to do by hand
    entry: dict = field(default_factory=dict)  # the manifest entry


def model_name(name: str) -> str:
    """The name in the manifest (model_set, MODEL_SET): the class name, first letter lowercase"""
    return name[0].lower() + name[1:]


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def check_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise WstsError(f"'{name}' is not a C++ class name: letters, digits and _, not starting with a digit")


def _parse_address(address: Optional[str], kind: str = "i2c") -> Optional[str]:
    if address is None:
        return None
    if address.strip().lower() == "any":
        return "any"
    try:
        value = int(address, 0)
    except ValueError:
        raise WstsError(f"--address '{address}' is not a number (0x48) or 'any'") from None
    if kind == "packet":
        if not 0x20 <= value <= 0xFF:
            raise WstsError(f"--address {address}: a SpaceWire logical address is 0x20..0xff (0..0x1f are path "
                            "addresses)")
    elif not 0 <= value <= 0x7F:
        raise WstsError(f"--address {address}: a 7-bit I2C address is 0x00..0x7f")
    return f"{value:#04x}"


def render_cpp(name: str, kind: str, namespace: str) -> tuple:
    summary, header, source = CPP[kind]
    values = {"Name": name, "NS": namespace, "GUARD": f"{namespace.upper()}_{snake(name).upper()}_HPP",
              "summary": summary}
    hpp = Template(HEADER_START + header + HEADER_END).substitute(values)
    cpp = Template(SOURCE_START + source + SOURCE_END).substitute(values)
    if kind in ("bytestream", "packet"):
        cpp = cpp.replace("#include <cmath>\n", "#include <cmath>\n#include <cstdio>\n")
    return hpp, cpp


def render_python(name: str, kind: str, endpoint: str, address: Optional[str]) -> str:
    summary, body = PY[kind]
    values = {"Name": name, "summary": summary, "endpoint": endpoint or "TODO",
              "address": address if address not in (None, "any")
              else '"any"  # TODO: its logical address' if kind == "packet" else "0x00  # TODO: the device's address"}
    return Template(PY_START + body).substitute(values)


def _add_to_module_cmake(path: Path, name: str, written: Written) -> None:
    source = f'        "${{CMAKE_CURRENT_LIST_DIR}}/{name}.cpp"'
    header = f'        "${{CMAKE_CURRENT_LIST_DIR}}/{name}.hpp"'
    if not path.exists():
        text = CMAKE_TEMPLATE
    else:
        text = path.read_text()
        if MARKER not in text:
            written.todo.append(f"add {name}.cpp (SOURCES) and {name}.hpp (HEADERS) to {path}: it was not "
                                "written by new-model, so it is left as it is")
            return
    if source in text:
        return
    # In the order the models were made: before the next section of the call
    text = text.replace("    HEADERS\n", f"{source}\n    HEADERS\n", 1).replace("    DEPENDS\n", f"{header}\n    DEPENDS\n", 1)
    path.write_text(text)
    written.files.append(str(path))


def _ensure_library(project: Path, written: Written) -> None:
    library = project / "library.cmake"
    if not library.exists():
        library.write_text(LIBRARY_CMAKE)
        written.files.append(str(library))
        return
    if not re.search(r'add_fprime_subdirectory\([^)]*/Models"?\s*\)', library.read_text()) and "Models" not in library.read_text():
        written.todo.append(f'make {library} build the models: add_fprime_subdirectory("${{CMAKE_CURRENT_LIST_DIR}}/Models")')


def _yaml_scalar(value) -> str:
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9_./:-]+", text) else '"' + text.replace('"', '\\"') + '"'


def _entry_text(entry: dict) -> str:
    return "  - {" + ", ".join(f"{k}: {_yaml_scalar(v)}" for k, v in entry.items()) + "}"


def _top_key(lines: List[str], key: str) -> Optional[int]:
    for i, line in enumerate(lines):
        if re.match(rf"{re.escape(key)}\s*:", line):
            return i
    return None


def _block_end(lines: List[str], start: int) -> int:
    """The line after the block that starts at `start` (a top-level key)"""
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#-"):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def add_to_list(text: str, path: List[str], item_line: str, item: str) -> Optional[str]:
    """Add an item to the list at `path` (one or two keys) of a manifest, as text. Returns the
    new text, or None when the list cannot be placed safely (written in another form)."""
    lines = text.split("\n")
    top = _top_key(lines, path[0])
    if top is None:
        if len(path) == 1:
            block = [f"{path[0]}:", item_line]
        else:
            block = [f"{path[0]}:", f"  {path[1]}: [{item}]"]
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines + [""] + block) + "\n"
    rest = lines[top].split(":", 1)[1].strip()
    if len(path) == 1:
        if rest in ("", "[]") or rest.startswith("#"):
            if rest == "[]":
                lines[top] = f"{path[0]}:"
            end = _block_end(lines, top)
            return "\n".join(lines[:end] + [item_line] + lines[end:])
        return None
    if rest and not rest.startswith("#"):
        return None  # a flow mapping
    end = _block_end(lines, top)
    for i in range(top + 1, end):
        m = re.match(rf"(\s+){re.escape(path[1])}\s*:\s*(.*)$", lines[i])
        if not m:
            continue
        value = m.group(2).split("#")[0].strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            lines[i] = f"{m.group(1)}{path[1]}: [{', '.join(items + [item])}]"
            return "\n".join(lines)
        if value == "":
            lines.insert(i + 1, f"{m.group(1)}  - {item}")
            return "\n".join(lines)
        return None
    indent = next((re.match(r"\s*", lines[i]).group(0) for i in range(top + 1, end) if lines[i].strip()
                   and not lines[i].lstrip().startswith("#")), "  ")
    lines.insert(top + 1, f"{indent}{path[1]}: [{item}]")
    return "\n".join(lines)


def _listed(data: dict, path: List[str], item: str) -> bool:
    value = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return False
        value = value[key]
    return isinstance(value, list) and item in [str(v) for v in value]


def edit_manifest(manifest: Path, entry: Optional[dict], python_file: Optional[str], written: Written) -> None:
    """Name the model in a manifest. What cannot be placed is left for the user (`written.todo`)."""
    from .manifest import parse_manifest

    original = manifest.read_text()
    text = original
    data = yaml.safe_load(text) or {}
    changes = []
    if entry is not None:
        names = [m.get("name") for m in data.get("models") or [] if isinstance(m, dict)]
        if entry["name"] in names:
            raise WstsError(f"{manifest} already has a model named '{entry['name']}': choose another name")
        new = add_to_list(text, ["models"], _entry_text(entry), "")
        if new is None:
            written.todo.append(f"add to models: in {manifest}:\n{_entry_text(entry)}")
        else:
            text, _ = new, changes.append("models")
        if not _listed(data, ["cmake", "depends"], MODULE):
            new = add_to_list(text, ["cmake", "depends"], "", MODULE)
            if new is None:
                written.todo.append(f"add {MODULE} to cmake.depends in {manifest}")
            else:
                text, _ = new, changes.append("cmake.depends")
    if python_file is not None and not _listed(data, ["run", "models"], python_file):
        new = add_to_list(text, ["run", "models"], "", python_file)
        if new is None:
            written.todo.append(f"add {python_file} to run.models in {manifest}")
        else:
            text, _ = new, changes.append("run.models")
    if text == original:
        return
    if not text.endswith("\n"):
        text += "\n"
    try:
        parse_manifest(yaml.safe_load(text) or {}, manifest)
    except Exception as error:  # the edit broke it: leave the manifest alone
        written.todo.append(f"name the model in {manifest} by hand (editing it as text gave: {error})")
        return
    manifest.write_text(text)
    written.manifest_edits += [f"{manifest}: {change}" for change in changes]


def find_manifest(project: Path) -> Optional[Path]:
    """The project's one deployment manifest, if it has exactly one"""
    candidates = []
    for path in sorted(project.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and "deployment" in data:
            candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def new_model(project: Path, name: str, kind: str, endpoint: Optional[str] = None, address: Optional[str] = None,
              python: bool = False, namespace: str = "Sim", manifest: Optional[Path] = None,
              edit: bool = True, params: Optional[dict] = None) -> Written:
    """Write a model into a simulation project and name it in a manifest (see the module's doc)"""
    check_name(name)
    if kind not in KINDS:
        raise WstsError(f"--kind must be one of {', '.join(KINDS)}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:]*", namespace):
        raise WstsError(f"'{namespace}' is not a C++ namespace")
    if address is not None and kind not in ("i2c", "packet"):
        raise WstsError("--address is for I2C and packet models only")
    address = _parse_address(address, kind)
    project = project.resolve()
    if not project.is_dir():
        raise WstsError(f"{project} is not a directory: give the simulation project")
    written = Written()

    if python:
        target = project / PY_DIR / f"{snake(name)}.py"
        if target.exists():
            raise WstsError(f"{target} exists already: choose another name, or remove it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_python(name, kind, endpoint or "", address))
        written.files.append(str(target))
        python_file = target.relative_to(project).as_posix()
        entry = None
        if not endpoint:
            written.todo.append(f"set ENDPOINT in {target}")
    else:
        directory = project / MODULE
        hpp_path, cpp_path = directory / f"{name}.hpp", directory / f"{name}.cpp"
        for path in (hpp_path, cpp_path):
            if path.exists():
                raise WstsError(f"{path} exists already: choose another name, or remove it")
        directory.mkdir(parents=True, exist_ok=True)
        hpp, cpp = render_cpp(name, kind, namespace)
        hpp_path.write_text(hpp)
        cpp_path.write_text(cpp)
        written.files += [str(hpp_path), str(cpp_path)]
        _add_to_module_cmake(directory / "CMakeLists.txt", name, written)
        _ensure_library(project, written)
        python_file = None
        entry = {"name": model_name(name), "class": f"{namespace}::{name}", "header": f"{MODULE}/{name}.hpp",
                 "endpoint": endpoint or "TODO", "kind": kind}
        if kind in ("i2c", "packet"):
            entry["address"] = address or "any"
        if params:
            entry["params"] = params
        written.entry = entry

    if not edit:
        return written
    if manifest is None:
        manifest = find_manifest(project)
    if manifest is None:
        snippet = (f"models:\n{_entry_text(entry)}\ncmake:\n  depends: [{MODULE}]" if entry
                   else f"run:\n  models: [{python_file}]")
        written.todo.append("name the model in the manifest of the deployment it is for (none given with "
                            f"--manifest, and the project has not exactly one):\n{snippet}")
    elif entry is not None and not endpoint:
        written.todo.append(f"name the model in {manifest} once its endpoint is known:\n"
                            f"models:\n{_entry_text(entry)}\ncmake:\n  depends: [{MODULE}]")
    else:
        edit_manifest(manifest.resolve(), entry, python_file, written)
    return written


def new_model_cli(args) -> int:
    """`fprime-wsts new-model NAME --kind KIND ...`"""
    import json

    written = new_model(Path(args.project), args.name, args.kind, args.endpoint, args.address, args.python,
                        args.namespace, Path(args.manifest) if args.manifest else None)
    if args.json:
        print(json.dumps({"files": written.files, "manifest": written.manifest_edits, "todo": written.todo,
                          "entry": written.entry}, indent=2))
        return 0
    for path in written.files:
        print(f"wrote {path}")
    for edit in written.manifest_edits:
        print(f"named the model in {edit}")
    steps = written.todo + [
        "fill in the TODOs of the model: registers, scale, commands, from the device's datasheet",
        "fprime-wsts build <manifest>, then run; change the value while it runs with "
        f"`fprime-wsts ctl <dir> model_set model={model_name(args.name)} key=value value=\"sine(offset=25, amplitude=5, period=60)\"`"
        if not args.python else "fprime-wsts run <generated dir>: the model starts with the deployment",
    ]
    print("\nNext:")
    for number, step in enumerate(steps, 1):
        print(f"  {number}. " + step.replace("\n", "\n     "))
    return 0
