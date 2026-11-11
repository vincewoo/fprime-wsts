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

}  // namespace Wsts
