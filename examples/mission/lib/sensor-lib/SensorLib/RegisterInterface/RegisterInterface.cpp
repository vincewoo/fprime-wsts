#include "SensorLib/RegisterInterface/RegisterInterface.hpp"

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <Fw/Logger/Logger.hpp>
#include <Fw/Types/Assert.hpp>

namespace SensorLib {

namespace {
// Stands in for the device's registers where the device does not exist
U32 hostRegisters[RegisterInterface::MAP_SIZE / sizeof(U32)];
}  // namespace

RegisterInterface::RegisterInterface(const char* compName)
    : RegisterInterfaceComponentBase(compName), m_base(hostRegisters), m_fd(-1), m_quit(false) {}

RegisterInterface::~RegisterInterface() {
    m_quit = true;
    if (m_fd >= 0) {
        (void)::close(m_fd);
        (void)m_task.join();
        (void)::munmap(const_cast<U32*>(m_base), MAP_SIZE);
    }
}

void RegisterInterface::configure(const char* device) {
    m_fd = ::open(device, O_RDWR | O_SYNC);
    if (m_fd < 0) {
        Fw::Logger::log("[WARNING] %s is not there: its registers are plain memory\n", device);
        return;
    }
    void* mapped = ::mmap(nullptr, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, m_fd, 0);
    FW_ASSERT(mapped != MAP_FAILED);
    m_base = static_cast<volatile U32*>(mapped);
    Os::Task::Arguments arguments(Fw::String("RegInterrupts"), RegisterInterface::waitForInterrupts, this);
    const Os::Task::Status status = m_task.start(arguments);
    FW_ASSERT(status == Os::Task::OP_OK, static_cast<FwAssertArgType>(status));
}

void RegisterInterface::waitForInterrupts(void* self) {
    auto* component = static_cast<RegisterInterface*>(self);
    U32 count = 0;
    // A read of a userspace I/O device returns when the device has interrupted
    while (!component->m_quit && ::read(component->m_fd, &count, sizeof count) == sizeof count) {
        if (component->isConnected_interrupt_OutputPort(0)) {
            component->interrupt_out(0, count);
        }
        const U32 enable = 1;
        (void)::write(component->m_fd, &enable, sizeof enable);
    }
}

U32 RegisterInterface::read32In_handler(FwIndexType portNum, U64 offset) {
    (void)portNum;
    FW_ASSERT(offset + sizeof(U32) <= MAP_SIZE, static_cast<FwAssertArgType>(offset));
    return m_base[offset / sizeof(U32)];
}

void RegisterInterface::write32In_handler(FwIndexType portNum, U64 offset, U32 value) {
    (void)portNum;
    FW_ASSERT(offset + sizeof(U32) <= MAP_SIZE, static_cast<FwAssertArgType>(offset));
    m_base[offset / sizeof(U32)] = value;
}

U64 RegisterInterface::read64In_handler(FwIndexType portNum, U64 offset) {
    (void)portNum;
    FW_ASSERT(offset + sizeof(U64) <= MAP_SIZE, static_cast<FwAssertArgType>(offset));
    return static_cast<U64>(m_base[offset / sizeof(U32)]) | (static_cast<U64>(m_base[offset / sizeof(U32) + 1]) << 32);
}

void RegisterInterface::write64In_handler(FwIndexType portNum, U64 offset, U64 value) {
    (void)portNum;
    FW_ASSERT(offset + sizeof(U64) <= MAP_SIZE, static_cast<FwAssertArgType>(offset));
    m_base[offset / sizeof(U32)] = static_cast<U32>(value);
    m_base[offset / sizeof(U32) + 1] = static_cast<U32>(value >> 32);
}

void RegisterInterface::REG_READ_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset) {
    if (offset + sizeof(U32) > MAP_SIZE) {
        this->log_WARNING_LO_OffsetOutOfRange(offset);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    this->log_ACTIVITY_HI_RegisterValue(offset, this->read32In_handler(0, offset));
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void RegisterInterface::REG_WRITE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset, U32 value) {
    if (offset + sizeof(U32) > MAP_SIZE) {
        this->log_WARNING_LO_OffsetOutOfRange(offset);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    this->write32In_handler(0, offset, value);
    this->log_ACTIVITY_HI_RegisterWritten(offset, value);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace SensorLib
