// ======================================================================
// \title  Bus.hpp
// \brief  WSTS simulation backplane
//
// The bus sits between surrogate components (the flight-software side) and device models
// (the simulated-hardware side). Endpoints are named: "uart0", "i2c1", "heaterPin". Every
// transaction passes through the fault table and is reported to the observer (recorder,
// bridge), which is what makes fault injection and record/replay independent of where a
// model lives.
// ======================================================================
#ifndef WSTS_CORE_BUS_HPP
#define WSTS_CORE_BUS_HPP

#include <atomic>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include <Wsts/Core/Json.hpp>

namespace Wsts {

// ----------------------------------------------------------------------
// Faults
// ----------------------------------------------------------------------

enum class FaultKind : std::uint8_t {
    DROP = 0,       //!< Discard the data / write (I2C and SPI: fail the transaction)
    CORRUPT = 1,    //!< XOR every byte travelling in the fault direction with (arg & 0xFF)
    DELAY = 2,      //!< Hold the transaction for arg milliseconds
    DUPLICATE = 3,  //!< Deliver byte stream data twice
    ERROR = 4,      //!< Return status arg to flight software without reaching the device
    STUCK = 5,      //!< Reads return arg (GPIO level, or fill byte); writes are ignored
};

enum class FaultDir : std::uint8_t {
    BOTH = 0,
    TO_DEVICE = 1,  //!< Flight software -> device
    TO_FLIGHT = 2,  //!< Device -> flight software
};

struct Fault {
    std::uint32_t id = 0;
    std::string endpoint;
    FaultKind kind = FaultKind::DROP;
    FaultDir dir = FaultDir::BOTH;
    std::uint32_t arg = 0;
    std::uint32_t count = 0;       //!< Remaining applications; 0 = unbounded
    std::uint64_t expireTick = 0;  //!< Tick at which the fault is removed; 0 = never
    std::uint32_t hits = 0;
};

const char* faultKindName(FaultKind kind);
bool faultKindFromName(const std::string& name, FaultKind& kind);
const char* faultDirName(FaultDir dir);
bool faultDirFromName(const std::string& name, FaultDir& dir);

// ----------------------------------------------------------------------
// Device model interfaces
// ----------------------------------------------------------------------

//! Where a device sends bytes back towards flight software
class ByteSink {
  public:
    virtual ~ByteSink() = default;
    virtual void send(const Bytes& data) = 0;
};

class ByteStreamDevice {
  public:
    virtual ~ByteStreamDevice() = default;
    //! Flight software transmitted `tx`. Reply (if any) through `reply`.
    virtual void onBytes(const Bytes& tx, ByteSink& reply) = 0;
};

class I2cDevice {
  public:
    virtual ~I2cDevice() = default;
    //! One I2C transaction. `read` arrives sized to the requested length. Return 0 on success,
    //! otherwise a Drv::I2cStatus value.
    virtual int transact(std::uint32_t addr, const Bytes& write, Bytes& read) = 0;
    //! Directly set a device register (used by the i2c_reg_set action). False if unsupported.
    virtual bool pokeRegister(std::uint8_t reg, std::uint8_t value) {
        (void)reg;
        (void)value;
        return false;
    }
};

class SpiDevice {
  public:
    virtual ~SpiDevice() = default;
    //! One SPI transfer. `read` arrives sized to the requested length. Return 0 on success,
    //! otherwise a Drv::SpiStatus value.
    virtual int transfer(const Bytes& write, Bytes& read) = 0;
};

class GpioDevice {
  public:
    virtual ~GpioDevice() = default;
    //! Flight software drove the pin
    virtual void onWrite(bool level) = 0;
};

// ----------------------------------------------------------------------
// Observation
// ----------------------------------------------------------------------

//! One bus transaction, as seen by the recorder and bridge subscribers
struct Record {
    std::uint64_t tick = 0;
    std::string type;      //!< bs_tx, bs_rx, i2c, spi, gpio_out, gpio_in
    std::string endpoint;
    std::string src;       //!< flight, model, link, inject, bridge, replay
    std::uint32_t addr = 0;
    Bytes data;            //!< bytes towards the device (or bs_rx payload)
    Bytes rdata;           //!< bytes read back (i2c, spi)
    int status = 0;
    bool level = false;    //!< gpio level
    JsonObj toJson() const;
};

class BusObserver {
  public:
    virtual ~BusObserver() = default;
    virtual void onRecord(const Record& record) = 0;
    //! A fault was applied. `first` on its first application, `expired` when it was its last.
    virtual void onFaultHit(const Fault& fault, bool first, bool expired) = 0;
};

struct BusCounters {
    std::uint64_t transactions = 0;
    std::uint64_t bytes = 0;
    std::uint64_t errors = 0;
    std::uint64_t faultHits = 0;
};

// ----------------------------------------------------------------------
// Bus
// ----------------------------------------------------------------------

class Bus {
  public:
    using RxHandler = std::function<void(const Bytes&)>;
    using EdgeHandler = std::function<void(bool)>;

    static Bus& get();

    // ---- Surrogate (flight software) side ----

    //! Register the handler that delivers device bytes to flight software
    void bindByteStream(const std::string& endpoint, RxHandler handler);
    //! Register the handler invoked when a GPIO input changes level
    void bindGpio(const std::string& endpoint, EdgeHandler handler);
    //! Remove surrogate handlers for an endpoint (surrogate teardown)
    void unbind(const std::string& endpoint);

    //! Flight software sends bytes. Returns 0, or a non-zero driver status from an ERROR fault.
    int byteStreamSend(const std::string& endpoint, const Bytes& data);
    int i2cTransact(const std::string& endpoint, std::uint32_t addr, const Bytes& write, Bytes& read);
    int spiTransfer(const std::string& endpoint, const Bytes& write, Bytes& read);
    int gpioWrite(const std::string& endpoint, bool level);
    int gpioRead(const std::string& endpoint, bool& level);

    // ---- Device (model) side ----

    void attachByteStream(const std::string& endpoint, ByteStreamDevice* device);
    void attachI2c(const std::string& endpoint, std::uint32_t addr, I2cDevice* device);
    void attachSpi(const std::string& endpoint, SpiDevice* device);
    void attachGpio(const std::string& endpoint, GpioDevice* device);
    void detach(const std::string& endpoint);
    //! Remove `device` from an endpoint if it is still the byte stream device there
    void detachByteStream(const std::string& endpoint, ByteStreamDevice* device);
    //! Whether a surrogate is bound to the flight side of a byte stream endpoint
    bool byteStreamBound(const std::string& endpoint);

    //! Bytes from the device (or an injection) towards flight software
    void byteStreamInject(const std::string& endpoint, const Bytes& data, const char* src = "model");
    //! Drive a GPIO input pin. Fires the surrogate's interrupt handler on a level change.
    void gpioSetInput(const std::string& endpoint, bool level, const char* src = "model");
    //! Last level flight software wrote to a pin
    bool gpioOutput(const std::string& endpoint);
    //! Poke a register on the I2C device at endpoint/addr
    bool i2cPoke(const std::string& endpoint, std::uint32_t addr, std::uint8_t reg, std::uint8_t value);

    bool known(const std::string& endpoint);
    std::vector<std::string> endpoints();

    // ---- Faults ----

    std::uint32_t addFault(const Fault& fault);
    //! Clear faults on an endpoint ("*" or "" clears all). Returns the number removed.
    std::uint32_t clearFaults(const std::string& endpoint);
    std::uint32_t activeFaults();
    //! Remove faults whose expiry tick has passed, reporting each to `expired`
    void expireFaults(std::uint64_t tick, std::vector<Fault>& expired);

    // ---- Replay of recorded device responses ----

    //! In replay mode device models are bypassed: I2C/SPI reads are served from the queued
    //! recorded responses, and byte stream / GPIO input comes only from injections.
    void setReplay(bool enabled);
    bool replay() const { return m_replay.load(); }
    //! Queue a recorded response. I2C responses are served in order per endpoint and
    //! address, so the devices sharing a bus each get their own; SPI uses address 0.
    void queueReplayResponse(const std::string& endpoint, std::uint32_t addr, int status, const Bytes& read);

    // ---- Housekeeping ----

    void setObserver(BusObserver* observer) { m_observer.store(observer); }
    void setTick(std::uint64_t tick) { m_tick.store(tick); }
    BusCounters counters();
    //! Drop all endpoints, devices, faults and counters (unit tests)
    void reset();

  private:
    struct Endpoint {
        RxHandler rx;
        EdgeHandler edge;
        ByteStreamDevice* byteStream = nullptr;
        std::map<std::uint32_t, I2cDevice*> i2c;
        SpiDevice* spi = nullptr;
        GpioDevice* gpio = nullptr;
        bool outLevel = false;
        bool inLevel = false;
        bool inDriven = false;
        //! Recorded responses by device address (0 for SPI)
        std::map<std::uint32_t, std::deque<std::pair<int, Bytes>>> replayResponses;
    };

    //! What the active faults ask for on one transaction
    struct Effects {
        bool drop = false;
        bool error = false;
        int status = 0;
        bool corrupt = false;
        std::uint8_t mask = 0;
        std::uint32_t delayMs = 0;
        bool duplicate = false;
        bool stuck = false;
        std::uint32_t stuckValue = 0;
    };

    class ReplySink;

    Bus() = default;
    Endpoint& endpointLocked(const std::string& name);
    Effects applyFaults(const std::string& endpoint, FaultDir dir);
    void emit(Record& record);
    void deliverToFlight(const std::string& endpoint, Bytes data, const char* src);

    std::mutex m_mutex;
    std::map<std::string, Endpoint> m_endpoints;
    std::vector<Fault> m_faults;
    std::uint32_t m_nextFaultId = 1;
    std::atomic<BusObserver*> m_observer{nullptr};
    std::atomic<std::uint64_t> m_tick{0};
    std::atomic<bool> m_replay{false};
    std::atomic<std::uint64_t> m_transactions{0};
    std::atomic<std::uint64_t> m_bytes{0};
    std::atomic<std::uint64_t> m_errors{0};
    std::atomic<std::uint64_t> m_faultHits{0};
};

}  // namespace Wsts

#endif
