// ======================================================================
// \title  Models.cpp
// \brief  Reference device models
// ======================================================================
#include <Wsts/Core/Models.hpp>

#include <sstream>

namespace Wsts {

void LoopbackByteStream::onBytes(const Bytes& tx, ByteSink& reply) {
    reply.send(tx);
}

bool ScriptedByteStream::set(const std::string& key, const std::string& value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (key == "endpoint") {
        m_endpoint = value;
        return true;
    }
    if (key == "text") {
        m_message.assign(value.begin(), value.end());
        return true;
    }
    if (key == "hex") {
        return fromHex(value, m_message);
    }
    if (key == "period") {
        std::int64_t period = 0;
        if (!parseInt(value, period) || period < 0) {
            return false;
        }
        m_period = static_cast<std::uint32_t>(period);
        return true;
    }
    return false;
}

void ScriptedByteStream::onTick(std::uint64_t tick) {
    Bytes message;
    std::string endpoint;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_period == 0 || m_message.empty() || m_endpoint.empty() || (tick % m_period) != 0) {
            return;
        }
        message = m_message;
        endpoint = m_endpoint;
    }
    Bus::get().byteStreamInject(endpoint, message, "model");
}

void ScriptedByteStream::onBytes(const Bytes& tx, ByteSink& reply) {
    (void)reply;
    std::lock_guard<std::mutex> lock(m_mutex);
    m_received.insert(m_received.end(), tx.begin(), tx.end());
}

Bytes ScriptedByteStream::received() {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_received;
}

bool RegisterMapI2c::set(const std::string& key, const std::string& value) {
    std::int64_t reg = 0;
    if (!parseInt(key, reg) || reg < 0 || reg > 255) {
        return false;
    }
    std::string spaced = value;
    for (char& c : spaced) {
        if (c == ',') {
            c = ' ';
        }
    }
    std::istringstream stream(spaced);
    std::string token;
    Bytes bytes;
    while (stream >> token) {
        std::int64_t byte = 0;
        if (!parseInt(token, byte) || byte < 0 || byte > 255) {
            return false;
        }
        bytes.push_back(static_cast<std::uint8_t>(byte));
    }
    if (bytes.empty()) {
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    for (std::size_t i = 0; i < bytes.size(); i++) {
        m_registers[(static_cast<std::size_t>(reg) + i) & 0xFF] = bytes[i];
    }
    return true;
}

int RegisterMapI2c::transact(std::uint32_t addr, const Bytes& write, Bytes& read) {
    (void)addr;
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!write.empty()) {
        m_pointer = write[0];
        for (std::size_t i = 1; i < write.size(); i++) {
            m_registers[m_pointer] = write[i];
            m_pointer = static_cast<std::uint8_t>(m_pointer + 1);
        }
    }
    for (std::uint8_t& byte : read) {
        byte = m_registers[m_pointer];
        m_pointer = static_cast<std::uint8_t>(m_pointer + 1);
    }
    return 0;
}

bool RegisterMapI2c::pokeRegister(std::uint8_t reg, std::uint8_t value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_registers[reg] = value;
    return true;
}

std::uint8_t RegisterMapI2c::peekRegister(std::uint8_t reg) {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_registers[reg];
}

int LoopbackSpi::transfer(const Bytes& write, Bytes& read) {
    for (std::size_t i = 0; i < read.size(); i++) {
        read[i] = (i < write.size()) ? write[i] : 0;
    }
    return 0;
}

// ----------------------------------------------------------------------
// RegisterFile
// ----------------------------------------------------------------------

RegisterFile::Register* RegisterFile::find(std::uint64_t offset, bool create) {
    const auto it = m_registers.find(offset);
    if (it != m_registers.end()) {
        return &it->second;
    }
    return create ? &m_registers[offset] : nullptr;
}

void RegisterFile::define(std::uint64_t offset, std::uint32_t reset, std::uint32_t writeMask, unsigned flags) {
    std::lock_guard<std::mutex> lock(m_mutex);
    Register& reg = m_registers[offset];
    reg.value = reset;
    reg.reset = reset;
    reg.writeMask = writeMask;
    reg.flags = flags;
}

void RegisterFile::onRead(std::uint64_t offset, Hook hook) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_registers[offset].readHook = std::move(hook);
}

void RegisterFile::onWrite(std::uint64_t offset, Hook hook) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_registers[offset].writeHook = std::move(hook);
}

std::uint32_t RegisterFile::peek(std::uint64_t offset) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const Register* reg = this->find(offset, false);
    return (reg == nullptr) ? 0 : reg->value;
}

void RegisterFile::setBits(std::uint64_t offset, std::uint32_t bits) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->find(offset, true)->value |= bits;
}

void RegisterFile::raise(std::uint32_t number) {
    std::string endpoint;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        endpoint = m_endpoint;
    }
    if (!endpoint.empty()) {
        Bus::get().registerInterrupt(endpoint, number, "model");
    }
}

bool RegisterFile::set(const std::string& key, const std::string& value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (key == "endpoint") {
        m_endpoint = value;
        return true;
    }
    std::uint64_t number = 0;
    if (!parseUInt(value, number)) {
        return false;
    }
    if (key == "strict") {
        m_strict = (number != 0);
        return true;
    }
    if (key == "reset") {
        if (number != 0) {
            for (auto& entry : m_registers) {
                entry.second.value = entry.second.reset;
            }
        }
        return true;
    }
    if (key == "irq.number" || key == "irq.period" || key == "irq.bits") {
        if (number > 0xFFFFFFFFull) {
            return false;
        }
        (key == "irq.number" ? m_irqNumber : key == "irq.period" ? m_irqPeriod : m_irqBits) = static_cast<std::uint32_t>(number);
        return true;
    }
    if (key == "irq.status") {
        m_irqStatus = number;
        m_irqHasStatus = true;
        (void)this->find(number, true);
        return true;
    }
    const std::size_t dot = key.find('.');
    std::uint64_t offset = 0;
    if (dot == std::string::npos || !parseUInt(key.substr(dot + 1), offset) || (offset % 4) != 0 ||
        number > 0xFFFFFFFFull) {
        return false;
    }
    const std::string field = key.substr(0, dot);
    const auto word = static_cast<std::uint32_t>(number);
    if (field == "reg") {
        const bool existed = m_registers.find(offset) != m_registers.end();
        Register& reg = m_registers[offset];
        reg.value = word;
        if (!existed) {
            reg.reset = word;
        }
    } else if (field == "reset") {
        this->find(offset, true)->reset = word;
    } else if (field == "mask") {
        this->find(offset, true)->writeMask = word;
    } else if (field == "w1c" || field == "rc") {
        Register& reg = *this->find(offset, true);
        const unsigned flag = (field == "w1c") ? WRITE_1_TO_CLEAR : READ_CLEARS;
        reg.flags = (word != 0) ? (reg.flags | flag) : (reg.flags & ~flag);
    } else {
        return false;
    }
    return true;
}

void RegisterFile::onTick(std::uint64_t tick) {
    std::uint32_t number = 0;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_irqPeriod == 0 || (tick % m_irqPeriod) != 0) {
            return;
        }
        if (m_irqHasStatus) {
            this->find(m_irqStatus, true)->value |= m_irqBits;
        }
        number = m_irqNumber;
    }
    this->raise(number);
}

int RegisterFile::read32(std::uint64_t offset, std::uint32_t& value, Hook& hook) {
    std::lock_guard<std::mutex> lock(m_mutex);
    Register* reg = this->find(offset, !m_strict);
    if (reg == nullptr) {
        return RegisterStatus::NO_DEVICE;
    }
    value = reg->value;
    if ((reg->flags & READ_CLEARS) != 0) {
        reg->value = 0;
    }
    hook = reg->readHook;
    return RegisterStatus::OK;
}

int RegisterFile::write32(std::uint64_t offset, std::uint32_t value, std::uint32_t& stored, Hook& hook) {
    std::lock_guard<std::mutex> lock(m_mutex);
    Register* reg = this->find(offset, !m_strict);
    if (reg == nullptr) {
        return RegisterStatus::NO_DEVICE;
    }
    if ((reg->flags & WRITE_1_TO_CLEAR) != 0) {
        reg->value &= ~(value & reg->writeMask);
    } else {
        reg->value = (reg->value & ~reg->writeMask) | (value & reg->writeMask);
    }
    stored = reg->value;
    hook = reg->writeHook;
    return RegisterStatus::OK;
}

int RegisterFile::read(std::uint64_t offset, unsigned width, std::uint64_t& value) {
    value = 0;
    for (unsigned half = 0; half < width / 4; half++) {
        std::uint32_t word = 0;
        Hook hook;
        const std::uint64_t at = offset + 4ull * half;
        const int status = this->read32(at, word, hook);
        if (status != RegisterStatus::OK) {
            return status;
        }
        if (hook) {
            hook(at, word);
        }
        value |= static_cast<std::uint64_t>(word) << (32 * half);
    }
    return RegisterStatus::OK;
}

int RegisterFile::write(std::uint64_t offset, unsigned width, std::uint64_t value) {
    for (unsigned half = 0; half < width / 4; half++) {
        std::uint32_t stored = 0;
        Hook hook;
        const std::uint64_t at = offset + 4ull * half;
        const int status = this->write32(at, static_cast<std::uint32_t>(value >> (32 * half)), stored, hook);
        if (status != RegisterStatus::OK) {
            return status;
        }
        if (hook) {
            hook(at, stored);
        }
    }
    return RegisterStatus::OK;
}

bool RegisterFile::poke(std::uint64_t offset, std::uint64_t value) {
    if ((offset % 4) != 0 || value > 0xFFFFFFFFull) {
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    this->find(offset, true)->value = static_cast<std::uint32_t>(value);
    return true;
}

}  // namespace Wsts
