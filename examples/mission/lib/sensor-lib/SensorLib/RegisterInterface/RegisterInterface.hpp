#ifndef SENSORLIB_REGISTER_INTERFACE_HPP
#define SENSORLIB_REGISTER_INTERFACE_HPP

#include <atomic>

#include <Os/Task.hpp>

#include "SensorLib/RegisterInterface/RegisterInterfaceComponentAc.hpp"

namespace SensorLib {

class RegisterInterface final : public RegisterInterfaceComponentBase {
  public:
    //! Size of a device's register block, in bytes
    static constexpr U64 MAP_SIZE = 4096;

    explicit RegisterInterface(const char* compName);
    ~RegisterInterface();

    //! Open the device (a userspace I/O device such as "/dev/uio0"), map its registers and
    //! start waiting for its interrupts. Where the device does not exist, which is every
    //! machine but the flight board, the registers are a block of memory in this process:
    //! what is written reads back, nothing else changes, and no interrupt arrives.
    void configure(const char* device);

  private:
    U32 read32In_handler(FwIndexType portNum, U64 offset) override;
    void write32In_handler(FwIndexType portNum, U64 offset, U32 value) override;
    U64 read64In_handler(FwIndexType portNum, U64 offset) override;
    void write64In_handler(FwIndexType portNum, U64 offset, U64 value) override;

    void REG_READ_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset) override;
    void REG_WRITE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset, U32 value) override;

    //! Blocks on the device until it interrupts, then calls the interrupt port
    static void waitForInterrupts(void* self);

    volatile U32* m_base;
    int m_fd;
    std::atomic<bool> m_quit;
    Os::Task m_task;
};

}  // namespace SensorLib

#endif
