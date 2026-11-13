// ======================================================================
// \title  RegisterSurrogate.cpp
// \brief  The bus side of a surrogate for a register interface component
// ======================================================================
#include <Wsts/Core/RegisterSurrogate.hpp>

#include <cstring>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

RegisterSurrogate::~RegisterSurrogate() {
    this->unbindRegister();
}

void RegisterSurrogate::bindRegister(const char* endpoint, const char* options, Deliver deliver) {
    m_endpoint = (endpoint != nullptr) ? endpoint : "";
    const char* text = (options != nullptr) ? options : "";
    const bool off = (std::strstr(text, "irq=off") != nullptr);
    m_serial = (std::strstr(text, "irq=direct") == nullptr);
    m_deliver = off ? Deliver() : std::move(deliver);
    Bus::get().bindRegister(m_endpoint, [this](std::uint32_t number) {
        if (!m_deliver) {
            return;
        }
        m_interrupts++;
        if (m_serial) {
            std::lock_guard<std::mutex> lock(m_deliverMutex);
            m_deliver(number);
        } else {
            m_deliver(number);
        }
    });
}

void RegisterSurrogate::unbindRegister() {
    if (!m_endpoint.empty()) {
        Bus::get().unbind(m_endpoint);
        // An interrupt that is being delivered finishes before the component goes away
        std::lock_guard<std::mutex> lock(m_deliverMutex);
        m_endpoint.clear();
    }
}

int RegisterSurrogate::registerRead(std::uint64_t offset, unsigned width, std::uint64_t& value) {
    const int status = Bus::get().registerRead(m_endpoint, offset, width, value);
    if (status != 0) {
        m_failures++;
    }
    return status;
}

int RegisterSurrogate::registerWrite(std::uint64_t offset, unsigned width, std::uint64_t value) {
    const int status = Bus::get().registerWrite(m_endpoint, offset, width, value);
    if (status != 0) {
        m_failures++;
    }
    return status;
}

std::uint32_t RegisterSurrogate::registerRead32(std::uint64_t offset) {
    std::uint64_t value = 0;
    (void)this->registerRead(offset, 4, value);
    return static_cast<std::uint32_t>(value);
}

std::uint64_t RegisterSurrogate::registerRead64(std::uint64_t offset) {
    std::uint64_t value = 0;
    (void)this->registerRead(offset, 8, value);
    return value;
}

void RegisterSurrogate::registerWrite32(std::uint64_t offset, std::uint32_t value) {
    (void)this->registerWrite(offset, 4, value);
}

void RegisterSurrogate::registerWrite64(std::uint64_t offset, std::uint64_t value) {
    (void)this->registerWrite(offset, 8, value);
}

}  // namespace Wsts
