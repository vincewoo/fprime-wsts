// ======================================================================
// \title  Bus.cpp
// \brief  WSTS simulation backplane implementation
// ======================================================================
#include <Wsts/Core/Bus.hpp>

#include <cctype>
#include <chrono>
#include <thread>

namespace Wsts {

namespace {

const char* const KIND_NAMES[] = {"drop", "corrupt", "delay", "duplicate", "error", "stuck"};
const char* const DIR_NAMES[] = {"both", "to_device", "to_flight"};

std::string lower(const std::string& in) {
    std::string out = in;
    for (char& c : out) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return out;
}

void corrupt(Bytes& data, std::uint8_t mask) {
    for (std::uint8_t& b : data) {
        b = static_cast<std::uint8_t>(b ^ mask);
    }
}

void delay(std::uint32_t ms) {
    if (ms > 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(ms));
    }
}

}  // namespace

const char* faultKindName(FaultKind kind) {
    return KIND_NAMES[static_cast<std::uint8_t>(kind) % 6];
}

bool faultKindFromName(const std::string& name, FaultKind& kind) {
    const std::string n = lower(name);
    for (std::uint8_t i = 0; i < 6; i++) {
        if (n == KIND_NAMES[i] || n == std::to_string(i)) {
            kind = static_cast<FaultKind>(i);
            return true;
        }
    }
    return false;
}

const char* faultDirName(FaultDir dir) {
    return DIR_NAMES[static_cast<std::uint8_t>(dir) % 3];
}

bool faultDirFromName(const std::string& name, FaultDir& dir) {
    const std::string n = lower(name);
    for (std::uint8_t i = 0; i < 3; i++) {
        if (n == DIR_NAMES[i] || n == std::to_string(i)) {
            dir = static_cast<FaultDir>(i);
            return true;
        }
    }
    return false;
}

JsonObj Record::toJson() const {
    JsonObj obj;
    obj.setNum("tick", static_cast<std::int64_t>(tick)).set("rec", type).set("endpoint", endpoint).set("src", src);
    if (type == "bs_tx" || type == "bs_rx") {
        obj.set("data", toHex(data));
    } else if (type == "i2c") {
        obj.setNum("addr", addr).set("write", toHex(data)).set("read", toHex(rdata)).setNum("status", status);
    } else if (type == "spi") {
        obj.set("write", toHex(data)).set("read", toHex(rdata)).setNum("status", status);
    } else {
        obj.setBool("level", level);
    }
    return obj;
}

//! Sink handed to byte stream devices so that replies flow back through the bus
class Bus::ReplySink : public ByteSink {
  public:
    ReplySink(Bus& bus, const std::string& endpoint) : m_bus(bus), m_endpoint(endpoint) {}
    void send(const Bytes& data) override { m_bus.byteStreamInject(m_endpoint, data, "model"); }

  private:
    Bus& m_bus;
    const std::string& m_endpoint;
};

Bus& Bus::get() {
    static Bus bus;
    return bus;
}

Bus::Endpoint& Bus::endpointLocked(const std::string& name) {
    return m_endpoints[name];
}

void Bus::bindByteStream(const std::string& endpoint, RxHandler handler) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).rx = std::move(handler);
}

void Bus::bindGpio(const std::string& endpoint, EdgeHandler handler) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).edge = std::move(handler);
}

void Bus::unbind(const std::string& endpoint) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto it = m_endpoints.find(endpoint);
    if (it != m_endpoints.end()) {
        it->second.rx = nullptr;
        it->second.edge = nullptr;
    }
}

void Bus::attachByteStream(const std::string& endpoint, ByteStreamDevice* device) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).byteStream = device;
}

void Bus::attachI2c(const std::string& endpoint, std::uint32_t addr, I2cDevice* device) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).i2c[addr] = device;
}

void Bus::attachSpi(const std::string& endpoint, SpiDevice* device) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).spi = device;
}

void Bus::attachGpio(const std::string& endpoint, GpioDevice* device) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).gpio = device;
}

void Bus::detach(const std::string& endpoint) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto it = m_endpoints.find(endpoint);
    if (it != m_endpoints.end()) {
        it->second.byteStream = nullptr;
        it->second.i2c.clear();
        it->second.spi = nullptr;
        it->second.gpio = nullptr;
    }
}

void Bus::detachByteStream(const std::string& endpoint, ByteStreamDevice* device) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto it = m_endpoints.find(endpoint);
    if (it != m_endpoints.end() && it->second.byteStream == device) {
        it->second.byteStream = nullptr;
    }
}

bool Bus::byteStreamBound(const std::string& endpoint) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto it = m_endpoints.find(endpoint);
    return it != m_endpoints.end() && static_cast<bool>(it->second.rx);
}

bool Bus::known(const std::string& endpoint) {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_endpoints.find(endpoint) != m_endpoints.end();
}

std::vector<std::string> Bus::endpoints() {
    std::lock_guard<std::mutex> lock(m_mutex);
    std::vector<std::string> names;
    for (const auto& entry : m_endpoints) {
        names.push_back(entry.first);
    }
    return names;
}

// ----------------------------------------------------------------------
// Faults
// ----------------------------------------------------------------------

std::uint32_t Bus::addFault(const Fault& fault) {
    std::lock_guard<std::mutex> lock(m_mutex);
    Fault copy = fault;
    copy.id = m_nextFaultId++;
    copy.hits = 0;
    m_faults.push_back(copy);
    return copy.id;
}

std::uint32_t Bus::clearFaults(const std::string& endpoint) {
    std::lock_guard<std::mutex> lock(m_mutex);
    std::uint32_t removed = 0;
    for (auto it = m_faults.begin(); it != m_faults.end();) {
        if (endpoint.empty() || endpoint == "*" || it->endpoint == endpoint) {
            it = m_faults.erase(it);
            removed++;
        } else {
            ++it;
        }
    }
    return removed;
}

std::uint32_t Bus::activeFaults() {
    std::lock_guard<std::mutex> lock(m_mutex);
    return static_cast<std::uint32_t>(m_faults.size());
}

void Bus::expireFaults(std::uint64_t tick, std::vector<Fault>& expired) {
    std::lock_guard<std::mutex> lock(m_mutex);
    for (auto it = m_faults.begin(); it != m_faults.end();) {
        if (it->expireTick != 0 && tick >= it->expireTick) {
            expired.push_back(*it);
            it = m_faults.erase(it);
        } else {
            ++it;
        }
    }
}

Bus::Effects Bus::applyFaults(const std::string& endpoint, FaultDir dir) {
    Effects effects;
    struct Hit {
        Fault fault;
        bool first;
        bool expired;
    };
    std::vector<Hit> hits;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        for (auto it = m_faults.begin(); it != m_faults.end();) {
            Fault& fault = *it;
            const bool dirMatch = (fault.dir == FaultDir::BOTH) || (fault.dir == dir);
            if (fault.endpoint != endpoint || !dirMatch) {
                ++it;
                continue;
            }
            switch (fault.kind) {
                case FaultKind::DROP:
                    effects.drop = true;
                    break;
                case FaultKind::CORRUPT:
                    effects.corrupt = true;
                    effects.mask = static_cast<std::uint8_t>(effects.mask ^ (fault.arg & 0xFF));
                    break;
                case FaultKind::DELAY:
                    effects.delayMs += fault.arg;
                    break;
                case FaultKind::DUPLICATE:
                    effects.duplicate = true;
                    break;
                case FaultKind::ERROR:
                    effects.error = true;
                    effects.status = (fault.arg == 0) ? 1 : static_cast<int>(fault.arg);
                    break;
                case FaultKind::STUCK:
                    effects.stuck = true;
                    effects.stuckValue = fault.arg;
                    break;
            }
            fault.hits++;
            bool expired = false;
            if (fault.count > 0) {
                fault.count--;
                expired = (fault.count == 0);
            }
            hits.push_back(Hit{fault, fault.hits == 1, expired});
            if (expired) {
                it = m_faults.erase(it);
            } else {
                ++it;
            }
        }
    }
    if (!hits.empty()) {
        m_faultHits += hits.size();
        BusObserver* observer = m_observer.load();
        if (observer != nullptr) {
            for (const Hit& hit : hits) {
                observer->onFaultHit(hit.fault, hit.first, hit.expired);
            }
        }
    }
    return effects;
}

void Bus::emit(Record& record) {
    record.tick = m_tick.load();
    m_transactions++;
    m_bytes += record.data.size() + record.rdata.size();
    if (record.status != 0) {
        m_errors++;
    }
    BusObserver* observer = m_observer.load();
    if (observer != nullptr) {
        observer->onRecord(record);
    }
}

// ----------------------------------------------------------------------
// Byte stream
// ----------------------------------------------------------------------

int Bus::byteStreamSend(const std::string& endpoint, const Bytes& data) {
    const Effects effects = this->applyFaults(endpoint, FaultDir::TO_DEVICE);
    delay(effects.delayMs);

    Record record;
    record.type = "bs_tx";
    record.endpoint = endpoint;
    record.src = "flight";
    record.data = data;
    if (effects.error) {
        record.status = effects.status;
        this->emit(record);
        return effects.status;
    }
    if (effects.corrupt) {
        corrupt(record.data, effects.mask);
    }
    this->emit(record);
    if (effects.drop || effects.stuck) {
        return 0;
    }
    ByteStreamDevice* device = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        device = this->endpointLocked(endpoint).byteStream;
    }
    // While responses are replayed a model is bypassed; the other end of a link is not
    if (device != nullptr && (!m_replay.load() || device->peer())) {
        ReplySink sink(*this, endpoint);
        device->onBytes(record.data, sink);
        if (effects.duplicate) {
            device->onBytes(record.data, sink);
        }
    }
    return 0;
}

void Bus::deliverToFlight(const std::string& endpoint, Bytes data, const char* src) {
    Record record;
    record.type = "bs_rx";
    record.endpoint = endpoint;
    record.src = src;
    record.data = std::move(data);
    this->emit(record);
    RxHandler handler;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        handler = this->endpointLocked(endpoint).rx;
    }
    if (handler) {
        handler(record.data);
    }
}

void Bus::byteStreamInject(const std::string& endpoint, const Bytes& data, const char* src) {
    // Recorded model output is replaced by the recording while replaying responses. What a
    // linked element sent is part of that recording too.
    if (m_replay.load() && (std::string(src) == "model" || std::string(src) == "link")) {
        return;
    }
    const Effects effects = this->applyFaults(endpoint, FaultDir::TO_FLIGHT);
    delay(effects.delayMs);
    if (effects.drop || effects.error || effects.stuck) {
        return;
    }
    Bytes copy = data;
    if (effects.corrupt) {
        corrupt(copy, effects.mask);
    }
    this->deliverToFlight(endpoint, copy, src);
    if (effects.duplicate) {
        this->deliverToFlight(endpoint, copy, src);
    }
}

// ----------------------------------------------------------------------
// I2C and SPI
// ----------------------------------------------------------------------

int Bus::i2cTransact(const std::string& endpoint, std::uint32_t addr, const Bytes& write, Bytes& read) {
    const Effects toDevice = this->applyFaults(endpoint, FaultDir::TO_DEVICE);
    delay(toDevice.delayMs);

    Record record;
    record.type = "i2c";
    record.endpoint = endpoint;
    record.src = "flight";
    record.addr = addr;
    record.data = write;

    if (toDevice.error || toDevice.drop) {
        // 2 = I2C_WRITE_ERR: a dropped transaction looks like a failed write to flight software
        record.status = toDevice.error ? toDevice.status : 2;
    } else if (m_replay.load()) {
        std::lock_guard<std::mutex> lock(m_mutex);
        auto& responses = this->endpointLocked(endpoint).replayResponses[addr];
        if (responses.empty()) {
            record.status = 5;  // I2C_OTHER_ERR: recording exhausted
        } else {
            record.status = responses.front().first;
            const Bytes& recorded = responses.front().second;
            for (std::size_t i = 0; i < read.size() && i < recorded.size(); i++) {
                read[i] = recorded[i];
            }
            responses.pop_front();
        }
        record.src = "replay";
    } else {
        I2cDevice* device = nullptr;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            Endpoint& ep = this->endpointLocked(endpoint);
            const auto it = ep.i2c.find(addr);
            device = (it == ep.i2c.end()) ? nullptr : it->second;
        }
        if (toDevice.corrupt) {
            corrupt(record.data, toDevice.mask);
        }
        // 1 = I2C_ADDRESS_ERR: nothing answers at this address
        record.status = (device == nullptr) ? 1 : device->transact(addr, record.data, read);
    }

    if (record.status == 0 && !read.empty()) {
        const Effects toFlight = this->applyFaults(endpoint, FaultDir::TO_FLIGHT);
        delay(toFlight.delayMs);
        if (toFlight.error || toFlight.drop) {
            record.status = toFlight.error ? toFlight.status : 3;  // 3 = I2C_READ_ERR
        }
        if (toFlight.corrupt) {
            corrupt(read, toFlight.mask);
        }
        if (toFlight.stuck) {
            read.assign(read.size(), static_cast<std::uint8_t>(toFlight.stuckValue & 0xFF));
        }
    }
    record.rdata = read;
    this->emit(record);
    return record.status;
}

int Bus::spiTransfer(const std::string& endpoint, const Bytes& write, Bytes& read) {
    const Effects toDevice = this->applyFaults(endpoint, FaultDir::TO_DEVICE);
    delay(toDevice.delayMs);

    Record record;
    record.type = "spi";
    record.endpoint = endpoint;
    record.src = "flight";
    record.data = write;

    if (toDevice.error || toDevice.drop) {
        record.status = toDevice.error ? toDevice.status : 4;  // 4 = SPI_WRITE_ERR
    } else if (m_replay.load()) {
        std::lock_guard<std::mutex> lock(m_mutex);
        auto& responses = this->endpointLocked(endpoint).replayResponses[0];
        if (responses.empty()) {
            record.status = 5;  // SPI_OTHER_ERR: recording exhausted
        } else {
            record.status = responses.front().first;
            const Bytes& recorded = responses.front().second;
            for (std::size_t i = 0; i < read.size() && i < recorded.size(); i++) {
                read[i] = recorded[i];
            }
            responses.pop_front();
        }
        record.src = "replay";
    } else {
        SpiDevice* device = nullptr;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            device = this->endpointLocked(endpoint).spi;
        }
        if (toDevice.corrupt) {
            corrupt(record.data, toDevice.mask);
        }
        // With no device attached MISO floats: the read comes back as zeros
        record.status = (device == nullptr) ? 0 : device->transfer(record.data, read);
    }

    if (record.status == 0 && !read.empty()) {
        const Effects toFlight = this->applyFaults(endpoint, FaultDir::TO_FLIGHT);
        delay(toFlight.delayMs);
        if (toFlight.error || toFlight.drop) {
            record.status = toFlight.error ? toFlight.status : 5;
        }
        if (toFlight.corrupt) {
            corrupt(read, toFlight.mask);
        }
        if (toFlight.stuck) {
            read.assign(read.size(), static_cast<std::uint8_t>(toFlight.stuckValue & 0xFF));
        }
    }
    record.rdata = read;
    this->emit(record);
    return record.status;
}

void Bus::queueReplayResponse(const std::string& endpoint, std::uint32_t addr, int status, const Bytes& read) {
    std::lock_guard<std::mutex> lock(m_mutex);
    this->endpointLocked(endpoint).replayResponses[addr].emplace_back(status, read);
}

void Bus::setReplay(bool enabled) {
    m_replay.store(enabled);
    if (!enabled) {
        std::lock_guard<std::mutex> lock(m_mutex);
        for (auto& entry : m_endpoints) {
            entry.second.replayResponses.clear();
        }
    }
}

bool Bus::i2cPoke(const std::string& endpoint, std::uint32_t addr, std::uint8_t reg, std::uint8_t value) {
    I2cDevice* device = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto ep = m_endpoints.find(endpoint);
        if (ep == m_endpoints.end()) {
            return false;
        }
        const auto it = ep->second.i2c.find(addr);
        device = (it == ep->second.i2c.end()) ? nullptr : it->second;
    }
    return (device != nullptr) && device->pokeRegister(reg, value);
}

// ----------------------------------------------------------------------
// GPIO
// ----------------------------------------------------------------------

int Bus::gpioWrite(const std::string& endpoint, bool level) {
    const Effects effects = this->applyFaults(endpoint, FaultDir::TO_DEVICE);
    delay(effects.delayMs);

    Record record;
    record.type = "gpio_out";
    record.endpoint = endpoint;
    record.src = "flight";
    record.level = level;
    if (effects.error) {
        record.status = effects.status;
        this->emit(record);
        return effects.status;
    }
    this->emit(record);
    if (effects.drop || effects.stuck) {
        return 0;
    }
    GpioDevice* device = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        Endpoint& ep = this->endpointLocked(endpoint);
        ep.outLevel = level;
        device = ep.gpio;
    }
    if (device != nullptr && !m_replay.load()) {
        device->onWrite(level);
    }
    return 0;
}

int Bus::gpioRead(const std::string& endpoint, bool& level) {
    const Effects effects = this->applyFaults(endpoint, FaultDir::TO_FLIGHT);
    delay(effects.delayMs);
    if (effects.error) {
        m_errors++;
        return effects.status;
    }
    if (effects.stuck) {
        level = (effects.stuckValue != 0);
        return 0;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const Endpoint& ep = this->endpointLocked(endpoint);
    // A pin nobody drives reads back what flight software last wrote
    level = ep.inDriven ? ep.inLevel : ep.outLevel;
    return 0;
}

void Bus::gpioSetInput(const std::string& endpoint, bool level, const char* src) {
    if (m_replay.load() && std::string(src) == "model") {
        return;
    }
    const Effects effects = this->applyFaults(endpoint, FaultDir::TO_FLIGHT);
    delay(effects.delayMs);
    if (effects.drop || effects.error || effects.stuck) {
        return;
    }
    EdgeHandler handler;
    bool changed = false;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        Endpoint& ep = this->endpointLocked(endpoint);
        const bool before = ep.inDriven ? ep.inLevel : ep.outLevel;
        ep.inLevel = level;
        ep.inDriven = true;
        changed = (before != level);
        handler = ep.edge;
    }
    Record record;
    record.type = "gpio_in";
    record.endpoint = endpoint;
    record.src = src;
    record.level = level;
    this->emit(record);
    if (changed && handler) {
        handler(level);
    }
}

bool Bus::gpioOutput(const std::string& endpoint) {
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto it = m_endpoints.find(endpoint);
    return (it != m_endpoints.end()) && it->second.outLevel;
}

// ----------------------------------------------------------------------
// Housekeeping
// ----------------------------------------------------------------------

BusCounters Bus::counters() {
    BusCounters counters;
    counters.transactions = m_transactions.load();
    counters.bytes = m_bytes.load();
    counters.errors = m_errors.load();
    counters.faultHits = m_faultHits.load();
    return counters;
}

void Bus::reset() {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_endpoints.clear();
    m_faults.clear();
    m_nextFaultId = 1;
    m_tick.store(0);
    m_replay.store(false);
    m_transactions.store(0);
    m_bytes.store(0);
    m_errors.store(0);
    m_faultHits.store(0);
}

}  // namespace Wsts
