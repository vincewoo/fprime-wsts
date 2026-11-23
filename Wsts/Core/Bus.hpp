// ======================================================================
// \title  Bus.hpp
// \brief  WSTS simulation backplane
//
// The bus sits between surrogate components (the flight-software side) and device models
// (the simulated-hardware side). Endpoints are named: "uart0", "i2c1", "heaterPin". There
// are six kinds of traffic: byte streams, I2C, SPI, GPIO, memory-mapped registers and packets
// (a SpaceWire network). Every
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
    DROP = 0,       //!< Discard the data / write (I2C and SPI: fail the transaction; register: a
                    //!< write is lost, a read fails, an interrupt is lost)
    CORRUPT = 1,    //!< XOR every byte travelling in the fault direction with (arg & 0xFF);
                    //!< a register value with all 32 bits of arg
    DELAY = 2,      //!< Hold the transaction for arg milliseconds
    DUPLICATE = 3,  //!< Deliver byte stream data, or a register interrupt, twice
    ERROR = 4,      //!< Return status arg to flight software without reaching the device
    STUCK = 5,      //!< Reads return arg (GPIO level, fill byte, register value); writes are ignored
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
    //! Whether this is the end of a link to another element of a system rather than a
    //! model. While recorded responses are replayed, models are bypassed, but what flight
    //! software sends still reaches a peer: that element is running live.
    virtual bool peer() const { return false; }
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

//! Where a packet device sends packets towards flight software
class PacketSink {
  public:
    virtual ~PacketSink() = default;
    virtual void send(const Bytes& packet) = 0;
};

//! A node of a packet network (SpaceWire): an instrument, an RMAP target, a router's far
//! side. The bus routes each packet flight software sends as SpaceWire routers do: the
//! leading path address bytes (0 to 31) are consumed on the way, and the first byte left,
//! the logical address (32 to 255), names the node. The node receives the packet from its
//! logical address on, which it keeps, as a SpaceWire node's receive buffer does.
//!
//! onPacket() is called on the flight thread that sent the packet. A node answers through
//! `reply`, during the call (an RMAP reply), and may also send unasked, at any time, with
//! Bus::packetInject (an instrument's science packets, from its onTick()).
class PacketDevice {
  public:
    virtual ~PacketDevice() = default;
    virtual void onPacket(const Bytes& packet, PacketSink& reply) = 0;
    //! As for byte streams: the end of a link to another element, which keeps answering
    //! while recorded responses are replayed
    virtual bool peer() const { return false; }
};

//! The logical address of a packet device that receives every logical address no other
//! device on the endpoint has
constexpr std::uint32_t PACKET_ANY_ADDRESS = 0xFFFFFFFFu;
//! The first logical address: lower bytes are path addresses (SpaceWire, ECSS-E-ST-50-12C)
constexpr std::uint8_t PACKET_FIRST_LOGICAL = 32;

//! Status of a register access: what a RegisterDevice returns, and what faults produce.
//! Flight register ports carry no status, so the surrogate sees it and flight software
//! does not: a failed read gives flight all ones (see Bus::registerRead).
struct RegisterStatus {
    enum : int {
        OK = 0,
        NO_DEVICE = 1,   //!< Nothing is attached to the endpoint, or nothing is mapped at the offset
        WRITE_ERR = 2,   //!< The write did not reach the device
        READ_ERR = 3,    //!< The read did not come back (a DROP fault)
        WIDTH_ERR = 4,   //!< The device does not support an access of this width
        OTHER_ERR = 5,   //!< An external model did not answer, or a recording is exhausted
    };
};

//! A block of memory-mapped registers: an FPGA block or an SoC peripheral that flight
//! software reaches through a register interface component. There is one device per
//! endpoint (a register block has no address on a bus).
//!
//! read() and write() are called on the flight thread that made the port call, poke() on
//! the thread of whoever executes the action (bridge, scenario, command), and a model's own
//! onTick() on the tick thread: a model guards its own state. The bus holds no lock while
//! it calls the device.
class RegisterDevice {
  public:
    virtual ~RegisterDevice() = default;
    //! Flight software read `width` bytes (4 or 8) at `offset`. Return 0, or a nonzero
    //! RegisterStatus. A device that has no 64-bit accesses returns WIDTH_ERR for width 8.
    virtual int read(std::uint64_t offset, unsigned width, std::uint64_t& value) = 0;
    //! Flight software wrote `value` (`width` bytes) at `offset`.
    virtual int write(std::uint64_t offset, unsigned width, std::uint64_t value) = 0;
    //! Directly set a register without going through a flight write (the reg_set action):
    //! no write mask, no write hook. False if unsupported.
    virtual bool poke(std::uint64_t offset, std::uint64_t value) {
        (void)offset;
        (void)value;
        return false;
    }
};

// ----------------------------------------------------------------------
// Observation
// ----------------------------------------------------------------------

//! One bus transaction, as seen by the recorder and bridge subscribers
struct Record {
    std::uint64_t tick = 0;
    std::string type;      //!< bs_tx, bs_rx, i2c, spi, gpio_out, gpio_in, reg_rd, reg_wr, reg_irq, pkt_tx, pkt_rx
    std::string endpoint;
    std::string src;       //!< flight, model, link, inject, bridge, replay
    std::uint64_t addr = 0;  //!< I2C address; register offset (reg_rd, reg_wr); interrupt number (reg_irq);
                             //!< logical address a packet was routed to (pkt_tx)
    Bytes data;            //!< bytes towards the device (or bs_rx payload); a written register
                           //!< value, little-endian, as many bytes as the access is wide
    Bytes rdata;           //!< bytes read back (i2c, spi); a register value read, little-endian
    int status = 0;
    bool level = false;    //!< gpio level
    //! Nothing answered: no device on the endpoint, none at the I2C address, or nothing at the
    //! register offset (RegisterStatus::NO_DEVICE from the bus or the model). Counted in the
    //! endpoint's traffic; not part of a recording.
    bool unanswered = false;
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

//! Traffic at one I2C address or register offset of an endpoint
struct AddressTraffic {
    std::uint64_t transactions = 0;
    std::uint64_t errors = 0;      //!< transactions that returned an error status
    std::uint64_t unanswered = 0;  //!< transactions nothing answered (Record::unanswered)
};

//! Traffic of one bus endpoint since start-up
struct EndpointTraffic {
    std::uint64_t transactions = 0;
    std::uint64_t toDevice = 0;       //!< transactions flight software made: sends, reads, writes, transfers, pin writes
    std::uint64_t toFlight = 0;       //!< what reached flight software unasked: received bytes, input levels, interrupts
    std::uint64_t bytesToDevice = 0;  //!< bytes sent, written or shifted out
    std::uint64_t bytesToFlight = 0;  //!< bytes received or read back
    std::uint64_t errors = 0;         //!< transactions that returned an error status
    std::uint64_t unanswered = 0;     //!< transactions nothing answered
    std::uint64_t faultHits = 0;      //!< transactions a fault was applied to (the endpoint's and its interrupts')
    std::uint64_t lastTick = 0;       //!< tick of the last transaction
    //! By I2C address (i2c), register offset (reg_rd, reg_wr) or logical address (pkt_tx)
    std::map<std::uint64_t, AddressTraffic> byAddress;
};

// ----------------------------------------------------------------------
// Bus
// ----------------------------------------------------------------------

//! The address of an I2C device that answers every address no other device on the endpoint has
constexpr std::uint32_t I2C_ANY_ADDRESS = 0xFFFFFFFFu;

class Bus {
  public:
    using RxHandler = std::function<void(const Bytes&)>;
    using EdgeHandler = std::function<void(bool)>;
    //! Called with the interrupt number when a register block raises an interrupt
    using InterruptHandler = std::function<void(std::uint32_t)>;

    static Bus& get();

    // ---- Surrogate (flight software) side ----

    //! Register the handler that delivers device bytes to flight software
    void bindByteStream(const std::string& endpoint, RxHandler handler);
    //! Register the handler invoked when a GPIO input changes level
    void bindGpio(const std::string& endpoint, EdgeHandler handler);
    //! Register the handler invoked when the register block on an endpoint raises an
    //! interrupt. The handler runs on the thread that raised it: the model's (usually the
    //! simulation's tick thread), a bridge client's or a scenario's. That is never the flight
    //! thread that reads and writes the registers, as a real interrupt context is not. What
    //! the handler calls must be safe to call from another thread.
    void bindRegister(const std::string& endpoint, InterruptHandler handler);
    //! Register the handler that delivers packets to flight software, one whole packet a call
    void bindPacket(const std::string& endpoint, RxHandler handler);
    //! Remove surrogate handlers for an endpoint (surrogate teardown)
    void unbind(const std::string& endpoint);

    //! Flight software sends bytes. Returns 0, or a non-zero driver status from an ERROR fault.
    int byteStreamSend(const std::string& endpoint, const Bytes& data);
    int i2cTransact(const std::string& endpoint, std::uint32_t addr, const Bytes& write, Bytes& read);
    int spiTransfer(const std::string& endpoint, const Bytes& write, Bytes& read);
    int gpioWrite(const std::string& endpoint, bool level);
    int gpioRead(const std::string& endpoint, bool& level);
    //! Flight software reads `width` bytes (4 or 8) of the register block on an endpoint.
    //! Returns 0, or a nonzero RegisterStatus (or the status of an ERROR fault). On any
    //! failure `value` is all ones over the width, which is what the surrogate hands to
    //! flight software: its read port has no status. The failure is counted in the bus
    //! errors. Reads take the faults armed in direction TO_FLIGHT (or BOTH).
    int registerRead(const std::string& endpoint, std::uint64_t offset, unsigned width, std::uint64_t& value);
    //! Flight software writes a register. Writes take the faults armed in direction
    //! TO_DEVICE (or BOTH). A dropped or stuck write returns 0: flight cannot tell.
    int registerWrite(const std::string& endpoint, std::uint64_t offset, unsigned width, std::uint64_t value);
    //! Flight software sends one packet: routed by its address bytes to the device at its
    //! logical address (see PacketDevice). Returns 0, or a non-zero driver status from an
    //! ERROR fault. A packet nothing receives (no logical address, no device there) is
    //! recorded as unanswered and returns 0: on a network, as on the wire, a sender cannot
    //! tell. Faults: DROP loses it, CORRUPT XORs every byte (addresses too), DELAY holds it,
    //! DUPLICATE delivers it twice, ERROR fails the send, STUCK loses it.
    int packetSend(const std::string& endpoint, const Bytes& packet);

    // ---- Device (model) side ----

    void attachByteStream(const std::string& endpoint, ByteStreamDevice* device);
    //! A device at `addr`, or at I2C_ANY_ADDRESS: the device for every address that has none
    //! of its own (a model that is attached before the device's address is known)
    void attachI2c(const std::string& endpoint, std::uint32_t addr, I2cDevice* device);
    void attachSpi(const std::string& endpoint, SpiDevice* device);
    void attachGpio(const std::string& endpoint, GpioDevice* device);
    //! One register block per endpoint: attaching another replaces the one before
    //! Returns the block that was attached before, or nullptr.
    RegisterDevice* attachRegister(const std::string& endpoint, RegisterDevice* device);
    //! Put `replacement` (which may be nullptr) on an endpoint if `device` is still the
    //! block there: how a model that stood in for another one gives the endpoint back
    void replaceRegister(const std::string& endpoint, RegisterDevice* device, RegisterDevice* replacement);
    void detach(const std::string& endpoint);
    //! Remove `device` from an endpoint if it is still the byte stream device there
    void detachByteStream(const std::string& endpoint, ByteStreamDevice* device);
    //! Remove `device` from an endpoint if it is still the device at `addr` there. The other
    //! addresses of the bus keep their devices, as do the other kinds on the endpoint
    void detachI2c(const std::string& endpoint, std::uint32_t addr, I2cDevice* device);
    //! Remove `device` from an endpoint if it is still the SPI device there
    void detachSpi(const std::string& endpoint, SpiDevice* device);
    //! Remove `device` from an endpoint if it is still the GPIO device there
    void detachGpio(const std::string& endpoint, GpioDevice* device);
    //! A node at logical address `addr` (32 to 255), or at PACKET_ANY_ADDRESS
    void attachPacket(const std::string& endpoint, std::uint32_t addr, PacketDevice* device);
    //! Remove `device` from an endpoint if it is still the node at `addr` there
    void detachPacket(const std::string& endpoint, std::uint32_t addr, PacketDevice* device);
    //! Whether a surrogate is bound to the flight side of a packet endpoint
    bool packetBound(const std::string& endpoint);
    //! Whether a surrogate is bound to the flight side of a byte stream endpoint
    bool byteStreamBound(const std::string& endpoint);

    //! Bytes from the device (or an injection) towards flight software
    void byteStreamInject(const std::string& endpoint, const Bytes& data, const char* src = "model");
    //! A packet towards flight software, from a node (or an injection). Faults in direction
    //! TO_FLIGHT apply as for packetSend.
    void packetInject(const std::string& endpoint, const Bytes& packet, const char* src = "model");
    //! Where a packet is routed: the index of its logical address after the path address
    //! bytes, and that address. False when no byte of it is a logical address.
    static bool packetRoute(const Bytes& packet, std::size_t& start, std::uint32_t& logical);
    //! Drive a GPIO input pin. Fires the surrogate's interrupt handler on a level change.
    void gpioSetInput(const std::string& endpoint, bool level, const char* src = "model");
    //! Last level flight software wrote to a pin
    bool gpioOutput(const std::string& endpoint);
    //! Poke a register on the I2C device at endpoint/addr
    bool i2cPoke(const std::string& endpoint, std::uint32_t addr, std::uint8_t reg, std::uint8_t value);

    //! Raise interrupt `number` of the register block on an endpoint: the surrogate's
    //! handler is called on this thread (see bindRegister). The interrupt is a record
    //! (reg_irq) and a fault target of its own, named "<endpoint>:irq": DROP loses it,
    //! DUPLICATE delivers it twice, DELAY holds it, CORRUPT XORs arg into the number. Faults
    //! on the endpoint itself act on register reads and writes only.
    void registerInterrupt(const std::string& endpoint, std::uint32_t number, const char* src = "model");
    //! Poke a register of the block on an endpoint (the reg_set action)
    bool registerPoke(const std::string& endpoint, std::uint64_t offset, std::uint64_t value);
    //! Name of the fault target for the interrupts of a register endpoint
    static std::string interruptTarget(const std::string& endpoint) { return endpoint + ":irq"; }

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
    //! Queue a recorded register read. Reads are served in order per endpoint and offset,
    //! so a register polled in another order than it was recorded still gets its own
    //! values. When the values recorded for an offset are used up, a read of it fails with
    //! OTHER_ERR (all ones to flight). Writes are recorded and go nowhere while replaying;
    //! interrupts come from the recording, not from the model.
    void queueRegisterReplay(const std::string& endpoint, std::uint64_t offset, int status, std::uint64_t value);

    // ---- Housekeeping ----

    void setObserver(BusObserver* observer) { m_observer.store(observer); }
    void setTick(std::uint64_t tick) { m_tick.store(tick); }
    BusCounters counters();
    //! Traffic of every endpoint that has had any, by endpoint name
    std::map<std::string, EndpointTraffic> traffic();
    //! Drop all endpoints, devices, faults and counters (unit tests)
    void reset();

  private:
    struct Endpoint {
        RxHandler rx;
        EdgeHandler edge;
        InterruptHandler irq;
        RegisterDevice* reg = nullptr;
        ByteStreamDevice* byteStream = nullptr;
        std::map<std::uint32_t, I2cDevice*> i2c;
        SpiDevice* spi = nullptr;
        GpioDevice* gpio = nullptr;
        RxHandler packetRx;
        std::map<std::uint32_t, PacketDevice*> packet;
        bool outLevel = false;
        bool inLevel = false;
        bool inDriven = false;
        //! Recorded responses by device address (0 for SPI)
        std::map<std::uint32_t, std::deque<std::pair<int, Bytes>>> replayResponses;
        //! Recorded register reads by offset
        std::map<std::uint64_t, std::deque<std::pair<int, std::uint64_t>>> registerReplay;
    };

    //! What the active faults ask for on one transaction
    struct Effects {
        bool drop = false;
        bool error = false;
        int status = 0;
        bool corrupt = false;
        std::uint8_t mask = 0;
        std::uint32_t wideMask = 0;  //!< all of arg, for register values
        std::uint32_t delayMs = 0;
        bool duplicate = false;
        bool stuck = false;
        std::uint32_t stuckValue = 0;
    };

    class ReplySink;
    class PacketReplySink;

    Bus() = default;
    Endpoint& endpointLocked(const std::string& name);
    Effects applyFaults(const std::string& endpoint, FaultDir dir);
    void emit(Record& record);
    void deliverToFlight(const std::string& endpoint, Bytes data, const char* src);
    void deliverPacketToFlight(const std::string& endpoint, Bytes packet, const char* src);

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
    //! Per endpoint; its own lock, taken on every transaction and never with m_mutex held
    std::mutex m_trafficMutex;
    std::map<std::string, EndpointTraffic> m_traffic;
};

}  // namespace Wsts

#endif
