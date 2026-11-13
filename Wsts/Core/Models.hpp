// ======================================================================
// \title  Models.hpp
// \brief  Reference device models shipped with the WSTS toolkit
//
// These cover the common cases and serve as templates for project-specific models. A
// model is plain C++: implement one of the device interfaces from Bus.hpp, optionally
// derive from Wsts::Model for parameters and ticks, and name the class in the manifest.
// ======================================================================
#ifndef WSTS_CORE_MODELS_HPP
#define WSTS_CORE_MODELS_HPP

#include <array>
#include <functional>
#include <map>
#include <mutex>
#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>

namespace Wsts {

//! Echoes every byte flight software sends
class LoopbackByteStream : public Model, public ByteStreamDevice {
  public:
    void onBytes(const Bytes& tx, ByteSink& reply) override;
};

//! Emits a fixed message every `period` ticks, like a free-running serial sensor.
//! Parameters: endpoint, text, hex, period (ticks; 0 disables).
class ScriptedByteStream : public Model, public ByteStreamDevice {
  public:
    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;
    void onBytes(const Bytes& tx, ByteSink& reply) override;
    //! Bytes flight software has sent to this device so far
    Bytes received();

  private:
    std::mutex m_mutex;
    std::string m_endpoint;
    Bytes m_message;
    std::uint32_t m_period = 0;
    Bytes m_received;
};

//! Register-addressed I2C device: a write sets the register pointer (first byte) and stores
//! any following bytes; a read returns bytes from the pointer, which auto-increments.
//! Parameters: "<register>" = "<byte> [<byte> ...]" (decimal or 0x hex), e.g. "0x75" = "0x68".
class RegisterMapI2c : public Model, public I2cDevice {
  public:
    bool set(const std::string& key, const std::string& value) override;
    int transact(std::uint32_t addr, const Bytes& write, Bytes& read) override;
    bool pokeRegister(std::uint8_t reg, std::uint8_t value) override;
    std::uint8_t peekRegister(std::uint8_t reg);

  private:
    std::mutex m_mutex;
    std::array<std::uint8_t, 256> m_registers{};
    std::uint8_t m_pointer = 0;
};

//! SPI device that shifts back what was shifted in
class LoopbackSpi : public Model, public SpiDevice {
  public:
    int transfer(const Bytes& write, Bytes& read) override;
};

//! A block of 32-bit memory-mapped registers: the starting point for the model of an FPGA
//! block or an SoC peripheral behind a register interface component.
//!
//! A register is defined by giving it a value (`reg.<offset>`), in the manifest or with
//! define(). Each has a reset value, a write mask (bits flight software can change), and
//! may be "write 1 to clear" or "read clears". A hook per register runs after a flight
//! read or write, outside the model's lock, so it may call back into the model or the bus.
//!
//! An access to an offset that was not defined fails with RegisterStatus::NO_DEVICE (flight
//! reads all ones, and the bus counts an error), which shows what the model still lacks.
//! With `strict` = 0 undefined offsets behave as plain memory instead.
//!
//! A 64-bit access is two registers: the low half at `offset`, the high half at
//! `offset + 4`. Offsets are multiples of 4.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET); numbers are decimal or 0x hex:
//!   reg.<offset>     value of a register, defining it on first use (also its reset value then)
//!   reset.<offset>   reset value
//!   mask.<offset>    write mask: bits flight software can write (default 0xFFFFFFFF)
//!   w1c.<offset>     1: writing a 1 clears the bit (within the write mask) instead of setting it
//!   rc.<offset>      1: a flight read returns the value and clears the register
//!   strict           1 (default): undefined offsets fail; 0: they are plain memory
//!   reset            1: every register back to its reset value
//!   irq.number       interrupt number raised by the periodic interrupt (default 0)
//!   irq.period       ticks between interrupts; 0 (default) for none
//!   irq.status       offset of the status register that gets `irq.bits` set before the
//!                    interrupt is raised (default: none)
//!   irq.bits         bits set in `irq.status` (default 1)
class RegisterFile : public Model, public RegisterDevice {
  public:
    //! Runs after a flight access: `value` is what was read, or the register's new value
    using Hook = std::function<void(std::uint64_t offset, std::uint32_t value)>;

    enum Flags : unsigned { NONE = 0, WRITE_1_TO_CLEAR = 1, READ_CLEARS = 2 };

    //! Define a register (or redefine it, keeping its hooks)
    void define(std::uint64_t offset, std::uint32_t reset, std::uint32_t writeMask = 0xFFFFFFFFu, unsigned flags = NONE);
    void onRead(std::uint64_t offset, Hook hook);
    void onWrite(std::uint64_t offset, Hook hook);
    //! Current value; 0 for a register that is not defined
    std::uint32_t peek(std::uint64_t offset);
    //! Set bits of a register as the hardware would (no write mask, no hook)
    void setBits(std::uint64_t offset, std::uint32_t bits);
    //! Raise an interrupt towards flight software on this model's endpoint
    void raise(std::uint32_t number);

    bool set(const std::string& key, const std::string& value) override;
    void onTick(std::uint64_t tick) override;
    int read(std::uint64_t offset, unsigned width, std::uint64_t& value) override;
    int write(std::uint64_t offset, unsigned width, std::uint64_t value) override;
    bool poke(std::uint64_t offset, std::uint64_t value) override;

  private:
    struct Register {
        std::uint32_t value = 0;
        std::uint32_t reset = 0;
        std::uint32_t writeMask = 0xFFFFFFFFu;
        unsigned flags = NONE;
        Hook readHook;
        Hook writeHook;
    };

    //! The register at `offset`, created if `create`; nullptr if it is not defined
    Register* find(std::uint64_t offset, bool create);
    int read32(std::uint64_t offset, std::uint32_t& value, Hook& hook);
    int write32(std::uint64_t offset, std::uint32_t value, std::uint32_t& stored, Hook& hook);

    std::mutex m_mutex;
    std::map<std::uint64_t, Register> m_registers;
    std::string m_endpoint;
    bool m_strict = true;
    std::uint32_t m_irqNumber = 0;
    std::uint32_t m_irqPeriod = 0;
    std::uint32_t m_irqBits = 1;
    bool m_irqHasStatus = false;
    std::uint64_t m_irqStatus = 0;
};

}  // namespace Wsts

#endif
