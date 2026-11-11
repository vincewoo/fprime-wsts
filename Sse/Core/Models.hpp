// ======================================================================
// \title  Models.hpp
// \brief  Reference device models shipped with the SSE toolkit
//
// These cover the common cases and serve as templates for project-specific models. A
// model is plain C++: implement one of the device interfaces from Bus.hpp, optionally
// derive from Sse::Model for parameters and ticks, and name the class in the manifest.
// ======================================================================
#ifndef SSE_CORE_MODELS_HPP
#define SSE_CORE_MODELS_HPP

#include <array>
#include <mutex>
#include <string>

#include <Sse/Core/Bus.hpp>
#include <Sse/Core/Control.hpp>

namespace Sse {

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

}  // namespace Sse

#endif
