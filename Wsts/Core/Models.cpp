// ======================================================================
// \title  Models.cpp
// \brief  Reference device models
// ======================================================================
#include <Wsts/Core/Models.hpp>

#include <algorithm>
#include <iterator>
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

bool ScriptedByteStream::get(const std::string& key, std::string& value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (key == "text") {
        value.assign(m_message.begin(), m_message.end());
    } else if (key == "hex") {
        value = toHex(m_message);
    } else if (key == "period") {
        value = std::to_string(m_period);
    } else {
        return false;
    }
    return true;
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
    bool numbers = true;
    while (stream >> token) {
        std::int64_t byte = 0;
        if (!parseInt(token, byte)) {
            numbers = false;
            break;
        }
        if (byte < 0 || byte > 255) {
            return false;  // a number, but not a byte
        }
        bytes.push_back(static_cast<std::uint8_t>(byte));
    }
    if (numbers) {
        if (bytes.empty()) {
            return false;
        }
        std::lock_guard<std::mutex> lock(m_mutex);
        this->endSignals(static_cast<std::uint8_t>(reg), bytes.size());
        for (std::size_t i = 0; i < bytes.size(); i++) {
            m_registers[(static_cast<std::size_t>(reg) + i) & 0xFF] = bytes[i];
        }
        return true;
    }
    // Not bytes: a signal, stored from this register on
    StoredSignal stored;
    std::string error;
    if (!StoredSignal::parse(value, stored, error) ||
        (stored.hasFormat && stored.format.bytes > 1 && !stored.format.explicitOrder)) {
        return false;
    }
    const std::uint64_t now = Control::get().elapsedNs();
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto first = static_cast<std::uint8_t>(reg);
    this->endSignals(first, stored.format.bytes);
    const std::vector<std::uint8_t> encoded = stored.format.toBytes(stored.signal.at(now));
    for (std::size_t i = 0; i < encoded.size(); i++) {
        m_registers[(first + i) & 0xFF] = encoded[i];
    }
    m_signals[first] = stored;
    return true;
}

void RegisterMapI2c::endSignals(std::uint8_t reg, std::size_t count) {
    for (auto it = m_signals.begin(); it != m_signals.end();) {
        // Both ranges wrap at 256, as the registers do
        bool overlaps = false;
        for (std::size_t i = 0; i < it->second.format.bytes && !overlaps; i++) {
            const std::size_t byte = (it->first + i) & 0xFF;
            overlaps = ((byte - reg) & 0xFF) < count;
        }
        it = overlaps ? m_signals.erase(it) : std::next(it);
    }
}

void RegisterMapI2c::update(std::uint64_t ns) {
    std::lock_guard<std::mutex> lock(m_mutex);
    for (const auto& entry : m_signals) {
        const std::vector<std::uint8_t> encoded = entry.second.format.toBytes(entry.second.signal.at(ns));
        for (std::size_t i = 0; i < encoded.size(); i++) {
            m_registers[(entry.first + i) & 0xFF] = encoded[i];
        }
    }
}

void RegisterMapI2c::onTick(std::uint64_t tick) {
    (void)tick;
    this->update(Control::get().elapsedNs());
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
    this->endSignals(reg, 1);
    m_registers[reg] = value;
    return true;
}

bool RegisterMapI2c::get(const std::string& key, std::string& value) {
    std::int64_t reg = 0;
    if (!parseInt(key, reg) || reg < 0 || reg > 255) {
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    value = std::to_string(m_registers[static_cast<std::size_t>(reg)]);
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

bool RegisterFile::peek(std::uint64_t offset, std::uint64_t& value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const Register* reg = this->find(offset, false);
    if (reg == nullptr) {
        return false;
    }
    value = reg->value;
    return true;
}

bool RegisterFile::get(const std::string& key, std::string& value) {
    std::uint64_t offset = 0;
    std::uint64_t word = 0;
    if (key.compare(0, 4, "reg.") != 0 || !parseUInt(key.substr(4), offset) || !this->peek(offset, word)) {
        return false;
    }
    value = std::to_string(word);
    return true;
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
    std::uint64_t number = 0;
    if (key.compare(0, 4, "reg.") == 0 && !parseUInt(value, number)) {
        // A signal, stored in the register on every tick
        std::uint64_t offset = 0;
        StoredSignal stored;
        std::string error;
        if (!parseUInt(key.substr(4), offset) || (offset % 4) != 0 || !StoredSignal::parse(value, stored, error) ||
            stored.format.explicitOrder) {
            return false;
        }
        if (!stored.hasFormat) {
            stored.format.bytes = 4;
        }
        const std::uint32_t word = stored.format.encode(stored.signal.at(Control::get().elapsedNs()));
        std::lock_guard<std::mutex> lock(m_mutex);
        const bool existed = m_registers.find(offset) != m_registers.end();
        Register& reg = m_registers[offset];
        reg.value = word;
        if (!existed) {
            reg.reset = word;
        }
        m_signals[offset] = stored;
        return true;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (key == "endpoint") {
        m_endpoint = value;
        return true;
    }
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
        m_signals.erase(offset);
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

void RegisterFile::update(std::uint64_t ns) {
    std::lock_guard<std::mutex> lock(m_mutex);
    for (const auto& entry : m_signals) {
        this->find(entry.first, true)->value = entry.second.format.encode(entry.second.signal.at(ns));
    }
}

void RegisterFile::onTick(std::uint64_t tick) {
    this->update(Control::get().elapsedNs());
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
    m_signals.erase(offset);
    this->find(offset, true)->value = static_cast<std::uint32_t>(value);
    return true;
}

}  // namespace Wsts

// ----------------------------------------------------------------------
// RMAP target (ECSS-E-ST-50-52C)
// ----------------------------------------------------------------------

namespace Wsts {

std::uint8_t rmapCrc(const std::uint8_t* data, std::size_t length) {
    // Reflected CRC-8, polynomial 0x07: bit by bit with the reflected polynomial 0xE0
    std::uint8_t crc = 0;
    for (std::size_t i = 0; i < length; i++) {
        crc = static_cast<std::uint8_t>(crc ^ data[i]);
        for (int bit = 0; bit < 8; bit++) {
            crc = static_cast<std::uint8_t>((crc & 1U) ? ((crc >> 1) ^ 0xE0U) : (crc >> 1));
        }
    }
    return crc;
}

namespace {

//! "0x12 0x34 56": bytes; false when any is not a number from 0 to 255 (or there is none)
bool parseByteList(const std::string& text, Bytes& out) {
    std::istringstream in(text);
    std::string word;
    out.clear();
    while (in >> word) {
        std::uint64_t value = 0;
        if (!parseUInt(word, value) || value > 0xFF) {
            out.clear();
            return false;
        }
        out.push_back(static_cast<std::uint8_t>(value));
    }
    return !out.empty();
}

std::uint32_t bigEndian(const Bytes& data, std::size_t at, std::size_t count) {
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < count; i++) {
        value = (value << 8) | data[at + i];
    }
    return value;
}

}  // namespace

RmapTarget::RmapTarget() : m_memory(4096, 0) {}

bool RmapTarget::inside(std::uint64_t address, std::size_t count) const {
    return address >= m_base && address - m_base <= m_memory.size() && count <= m_memory.size() - (address - m_base);
}

bool RmapTarget::get(const std::string& key, std::string& value) {
    std::uint64_t address = 0;
    if (key.rfind("mem.", 0) != 0 || !parseUInt(key.substr(4), address)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!this->inside(address, 1)) {
        return false;
    }
    value = std::to_string(m_memory[static_cast<std::size_t>(address - m_base)]);
    return true;
}

bool RmapTarget::set(const std::string& key, const std::string& value) {
    std::uint64_t number = 0;
    if (key == "key") {
        if (!parseUInt(value, number) || number > 0xFF) {
            return false;
        }
        std::lock_guard<std::mutex> lock(m_mutex);
        m_key = static_cast<std::uint8_t>(number);
        return true;
    }
    if (key == "base" || key == "size") {
        if (!parseUInt(value, number) || (key == "size" && (number == 0 || number > (64U << 20)))) {
            return false;
        }
        std::lock_guard<std::mutex> lock(m_mutex);
        if (key == "base") {
            m_base = number;
        } else {
            m_memory.resize(static_cast<std::size_t>(number), 0);
        }
        m_signals.clear();  // their addresses meant other memory
        return true;
    }
    if (key.rfind("mem.", 0) != 0 || !parseUInt(key.substr(4), number)) {
        return false;
    }
    const std::uint64_t address = number;
    Bytes data;
    if (parseByteList(value, data)) {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!this->inside(address, data.size())) {
            return false;
        }
        for (auto it = m_signals.begin(); it != m_signals.end();) {
            const bool overlaps = it->first < address + data.size() && address < it->first + it->second.format.bytes;
            it = overlaps ? m_signals.erase(it) : std::next(it);
        }
        std::copy(data.begin(), data.end(), m_memory.begin() + static_cast<std::ptrdiff_t>(address - m_base));
        return true;
    }
    // Not bytes: a signal, stored from this address on
    StoredSignal stored;
    std::string error;
    if (!StoredSignal::parse(value, stored, error) ||
        (stored.hasFormat && stored.format.bytes > 1 && !stored.format.explicitOrder)) {
        return false;
    }
    const std::uint64_t now = Control::get().elapsedNs();
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!this->inside(address, stored.format.bytes)) {
        return false;
    }
    m_signals[address] = stored;
    const Bytes encoded = stored.format.toBytes(stored.signal.at(now));
    std::copy(encoded.begin(), encoded.end(), m_memory.begin() + static_cast<std::ptrdiff_t>(address - m_base));
    return true;
}

void RmapTarget::storeSignals(std::uint64_t ns) {
    std::lock_guard<std::mutex> lock(m_mutex);
    for (const auto& entry : m_signals) {
        const Bytes encoded = entry.second.format.toBytes(entry.second.signal.at(ns));
        if (this->inside(entry.first, encoded.size())) {
            std::copy(encoded.begin(), encoded.end(), m_memory.begin() + static_cast<std::ptrdiff_t>(entry.first - m_base));
        }
    }
}

void RmapTarget::onTick(std::uint64_t tick) {
    (void)tick;
    this->storeSignals(Control::get().elapsedNs());
}

Bytes RmapTarget::peek(std::uint64_t address, std::size_t count) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!this->inside(address, count)) {
        return Bytes();
    }
    const auto first = m_memory.begin() + static_cast<std::ptrdiff_t>(address - m_base);
    return Bytes(first, first + static_cast<std::ptrdiff_t>(count));
}

void RmapTarget::onPacket(const Bytes& packet, PacketSink& reply) {
    // Target logical address, protocol identifier, instruction, key
    if (packet.size() < 4 || packet[1] != 0x01 || (packet[2] >> 6) != 0x1) {
        m_discarded++;  // not RMAP, or not a command
        return;
    }
    const std::uint8_t instruction = packet[2];
    const std::size_t replyAddressLength = static_cast<std::size_t>(instruction & 0x3U) * 4;
    // Reply address, initiator logical address, transaction ID, extended address, address, data length
    const std::size_t headerLength = 4 + replyAddressLength + 1 + 2 + 1 + 4 + 3;
    if (packet.size() < headerLength + 1 || rmapCrc(packet.data(), headerLength) != packet[headerLength]) {
        m_discarded++;  // a header that ends early or is corrupt: no reply can be trusted
        return;
    }
    m_commands++;
    Bytes replyAddress(packet.begin() + 4, packet.begin() + 4 + static_cast<std::ptrdiff_t>(replyAddressLength));
    while (!replyAddress.empty() && replyAddress.front() == 0) {
        replyAddress.erase(replyAddress.begin());  // leading zeros pad the field
    }
    const std::size_t at = 4 + replyAddressLength;
    const std::uint8_t initiator = packet[at];
    const std::uint8_t transactionHigh = packet[at + 1];
    const std::uint8_t transactionLow = packet[at + 2];
    const std::uint64_t address = (static_cast<std::uint64_t>(packet[at + 3]) << 32) | bigEndian(packet, at + 4, 4);
    const std::size_t length = bigEndian(packet, at + 8, 3);
    const Bytes cargo(packet.begin() + static_cast<std::ptrdiff_t>(headerLength + 1), packet.end());

    // The command field: write, verify, reply, increment
    const unsigned code = (instruction >> 2) & 0xFU;
    const bool write = (code & 0x8U) != 0;
    const bool verify = (code & 0x4U) != 0;
    const bool acknowledge = (code & 0x2U) != 0;
    const bool increment = (code & 0x1U) != 0;
    const bool read = !write && !verify && acknowledge;  // 0010, 0011
    const bool modify = (code == 0x7U);                  // read-modify-write
    std::uint8_t status = SUCCESS;
    Bytes returned;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!write && !read && !modify) {
            status = UNUSED_COMMAND;
        } else if (packet[3] != m_key) {
            status = INVALID_KEY;
        } else if (read) {
            if (!cargo.empty()) {
                status = TOO_MUCH_DATA;
            } else if (!this->inside(address, increment ? length : (length > 0 ? 1 : 0))) {
                status = NOT_AUTHORISED;
            } else {
                for (std::size_t i = 0; i < length; i++) {
                    returned.push_back(m_memory[static_cast<std::size_t>(address - m_base) + (increment ? i : 0)]);
                }
            }
        } else if (cargo.size() < length + 1) {
            status = EARLY_EOP;
        } else if (cargo.size() > length + 1) {
            status = TOO_MUCH_DATA;
        } else {
            const bool crcOk = rmapCrc(cargo.data(), length) == cargo[length];
            if (modify) {
                const std::size_t half = length / 2;
                if ((length % 2) != 0 || half > 4) {
                    status = RMW_DATA_LENGTH;
                } else if (!this->inside(address, half)) {
                    status = NOT_AUTHORISED;
                } else if (!crcOk) {
                    status = INVALID_DATA_CRC;  // verified: nothing is written
                } else {
                    const std::size_t offset = static_cast<std::size_t>(address - m_base);
                    for (std::size_t i = 0; i < half; i++) {
                        const std::uint8_t old = m_memory[offset + i];
                        returned.push_back(old);
                        m_memory[offset + i] = static_cast<std::uint8_t>((cargo[i] & cargo[half + i]) | (old & ~cargo[half + i]));
                    }
                    m_signals.erase(address);
                }
            } else if (!this->inside(address, increment ? length : (length > 0 ? 1 : 0))) {
                status = NOT_AUTHORISED;
            } else if (verify && !crcOk) {
                status = INVALID_DATA_CRC;  // checked before it is written
            } else {
                const std::size_t offset = static_cast<std::size_t>(address - m_base);
                for (std::size_t i = 0; i < length; i++) {
                    m_memory[offset + (increment ? i : 0)] = cargo[i];
                }
                for (auto it = m_signals.begin(); it != m_signals.end();) {
                    const std::size_t span = increment ? length : 1;
                    const bool overlaps = it->first < address + span && address < it->first + it->second.format.bytes;
                    it = overlaps ? m_signals.erase(it) : std::next(it);
                }
                if (!crcOk) {
                    status = INVALID_DATA_CRC;  // written already, as an unverified write is
                }
            }
        }
    }
    if (!acknowledge) {
        return;  // a write that asks for no reply
    }
    // Reply: the path back, then initiator, protocol, instruction as a reply, status, target,
    // transaction ID; a read or read-modify-write reply has its data length and data
    Bytes out = replyAddress;
    const std::size_t headerStart = out.size();
    out.push_back(initiator);
    out.push_back(0x01);
    out.push_back(static_cast<std::uint8_t>(instruction & 0x3FU));
    out.push_back(status);
    out.push_back(packet[0]);
    out.push_back(transactionHigh);
    out.push_back(transactionLow);
    if (write) {
        out.push_back(rmapCrc(out.data() + headerStart, out.size() - headerStart));
    } else {
        out.push_back(0x00);  // reserved
        out.push_back(static_cast<std::uint8_t>(returned.size() >> 16));
        out.push_back(static_cast<std::uint8_t>(returned.size() >> 8));
        out.push_back(static_cast<std::uint8_t>(returned.size()));
        out.push_back(rmapCrc(out.data() + headerStart, out.size() - headerStart));
        out.insert(out.end(), returned.begin(), returned.end());
        out.push_back(rmapCrc(returned));
    }
    reply.send(out);
}

}  // namespace Wsts
