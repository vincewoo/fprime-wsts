// ======================================================================
// \title  RegisterSurrogate.hpp
// \brief  The bus side of a surrogate for a register interface component
//
// A register interface component is what flight device drivers read and write 32-bit and
// 64-bit registers through, and what delivers the hardware's interrupts to them. Its port
// types are a project's own, so the F Prime component that stands in for it is the
// project's too (or one the generator writes for it, see Wsts.SimRegister). What that
// component does on the bus is the same every time, and is here:
//
//   class SimRegif final : public SimRegifComponentBase, public Wsts::RegisterSurrogate {
//     public:
//       void wstsBind(const char* endpoint, const char* options = "") {
//           this->bindRegister(endpoint, options, [this](std::uint32_t n) { this->interrupt_out(0, n); });
//       }
//     private:
//       U32 read32In_handler(FwIndexType, U64 offset) override { return this->registerRead32(offset); }
//       void write32In_handler(FwIndexType, U64 offset, U32 value) override { this->registerWrite32(offset, value); }
//   };
//
// A mirror surrogate adds the flight component's commands and events on top, with
// registerRead() and registerWrite() for commands that report whether the access worked.
// ======================================================================
#ifndef WSTS_CORE_REGISTER_SURROGATE_HPP
#define WSTS_CORE_REGISTER_SURROGATE_HPP

#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

namespace Wsts {

class RegisterSurrogate {
  public:
    //! Delivers one interrupt to flight software: calls the component's interrupt output port
    using Deliver = std::function<void(std::uint32_t number)>;

    RegisterSurrogate() = default;
    ~RegisterSurrogate();
    RegisterSurrogate(const RegisterSurrogate&) = delete;
    RegisterSurrogate& operator=(const RegisterSurrogate&) = delete;

    //! Bind to a bus endpoint. `deliver` is called for every interrupt the register block
    //! raises, on the thread that raised it (the simulation's tick thread for a model, a
    //! bridge thread, a scenario): never the flight thread that reads and writes registers,
    //! as in flight the interrupt context is not. Calls are serialised, one interrupt at a
    //! time, so a flight handler written for an interrupt context that cannot be re-entered
    //! is not re-entered here. A handler must still not block on the thread that raised
    //! the interrupt: a flight handler that only posts a message to its component's queue
    //! is the shape this is for.
    //!
    //! Options (space separated):
    //!   irq=serial   deliver one interrupt at a time (the default)
    //!   irq=direct   call `deliver` without the lock, when the flight handler is re-entrant
    //!   irq=off      deliver nothing
    void bindRegister(const char* endpoint, const char* options, Deliver deliver);
    void unbindRegister();

    // ---- For the port handlers. A read that fails returns all ones: the ports have no status ----

    std::uint32_t registerRead32(std::uint64_t offset);
    std::uint64_t registerRead64(std::uint64_t offset);
    void registerWrite32(std::uint64_t offset, std::uint32_t value);
    void registerWrite64(std::uint64_t offset, std::uint64_t value);

    // ---- For commands of a mirror surrogate: the same accesses, with their status ----

    //! Returns 0 or a nonzero Wsts::RegisterStatus; `value` is all ones on failure
    int registerRead(std::uint64_t offset, unsigned width, std::uint64_t& value);
    int registerWrite(std::uint64_t offset, unsigned width, std::uint64_t value);

    const std::string& registerEndpoint() const { return m_endpoint; }
    //! Accesses that failed since start-up (they are also in the bus error counter)
    std::uint64_t registerFailures() const { return m_failures.load(); }
    //! Interrupts delivered to flight software
    std::uint64_t registerInterrupts() const { return m_interrupts.load(); }

  private:
    std::string m_endpoint;
    Deliver m_deliver;
    bool m_serial = true;
    std::mutex m_deliverMutex;
    std::atomic<std::uint64_t> m_failures{0};
    std::atomic<std::uint64_t> m_interrupts{0};
};

}  // namespace Wsts

#endif
