// ======================================================================
// \title  Bridge.cpp
// \brief  Socket bridge implementation
// ======================================================================
#include <Wsts/Core/Bridge.hpp>

#include <Wsts/Core/Activity.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <map>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Link.hpp>
#include <Wsts/Core/LinkRegistry.hpp>

namespace Wsts {

// ----------------------------------------------------------------------
// One connected client
// ----------------------------------------------------------------------

class Bridge::Client : public std::enable_shared_from_this<Bridge::Client> {
  public:
    Client(int fd, unsigned rpcTimeoutMs) : m_fd(fd), m_rpcTimeoutMs(rpcTimeoutMs) {}

    ~Client() {
        if (m_thread.joinable()) {
            m_thread.join();
        }
        ::close(m_fd);
    }

    void start() {
        m_thread = std::thread([this]() { this->readLoop(); });
    }

    void shutdown() { ::shutdown(m_fd, SHUT_RDWR); }
    bool done() const { return m_done.load(); }
    bool subscribed() const { return m_subscribed.load(); }

    void send(const JsonObj& obj) {
        const std::string line = obj.dump() + "\n";
        std::lock_guard<std::mutex> lock(m_sendMutex);
        std::size_t sent = 0;
        while (sent < line.size()) {
            const ssize_t n = ::send(m_fd, line.data() + sent, line.size() - sent, MSG_NOSIGNAL);
            if (n <= 0) {
                return;
            }
            sent += static_cast<std::size_t>(n);
        }
    }

    //! Ask the external model to perform a transaction and wait for its reply
    int rpc(JsonObj request, Bytes& read) {
        std::uint32_t rid = 0;
        {
            std::lock_guard<std::mutex> lock(m_rpcMutex);
            rid = m_nextRid++;
            m_pending[rid] = Pending{};
        }
        request.setNum("rid", rid).setNum("rlen", static_cast<std::int64_t>(read.size()));
        this->send(request);

        std::unique_lock<std::mutex> lock(m_rpcMutex);
        const bool answered = m_rpcCv.wait_for(lock, std::chrono::milliseconds(m_rpcTimeoutMs), [this, rid]() {
            return m_pending[rid].answered || m_done.load();
        });
        const Pending pending = m_pending[rid];
        m_pending.erase(rid);
        if (!answered || !pending.answered) {
            return 5;  // *_OTHER_ERR: the external model did not answer
        }
        for (std::size_t i = 0; i < read.size() && i < pending.data.size(); i++) {
            read[i] = pending.data[i];
        }
        return pending.status;
    }

    //! As rpc(), for an answer of any length: what the model sent back, whole
    int rpcData(JsonObj request, Bytes& data) {
        std::uint32_t rid = 0;
        {
            std::lock_guard<std::mutex> lock(m_rpcMutex);
            rid = m_nextRid++;
            m_pending[rid] = Pending{};
        }
        request.setNum("rid", rid).setNum("rlen", 0);
        this->send(request);
        std::unique_lock<std::mutex> lock(m_rpcMutex);
        const bool answered = m_rpcCv.wait_for(lock, std::chrono::milliseconds(m_rpcTimeoutMs), [this, rid]() {
            return m_pending[rid].answered || m_done.load();
        });
        const Pending pending = m_pending[rid];
        m_pending.erase(rid);
        if (!answered || !pending.answered) {
            data.clear();
            return 5;
        }
        data = pending.data;
        return pending.status;
    }

  private:
    struct Pending {
        bool answered = false;
        int status = 0;
        Bytes data;
    };

    //! A node of a packet network whose behaviour is supplied by this client. The client
    //! answers each packet with the reply packet, or with nothing; what it sends unasked
    //! goes through inject_packet.
    class RemotePacket : public PacketDevice {
      public:
        RemotePacket(Client& client, std::string endpoint) : m_client(client), m_endpoint(std::move(endpoint)) {}
        void onPacket(const Bytes& packet, PacketSink& reply) override {
            JsonObj request;
            request.set("ev", "pkt_req").set("endpoint", m_endpoint).setNum("addr", packet.empty() ? 0 : packet[0]);
            request.set("data", toHex(packet));
            Bytes answer;
            if (m_client.rpcData(request, answer) == 0 && !answer.empty()) {
                reply.send(answer);
            }
        }

      private:
        Client& m_client;
        std::string m_endpoint;
    };

    //! I2C device whose behaviour is supplied by this client
    class RemoteI2c : public I2cDevice {
      public:
        RemoteI2c(Client& client, std::string endpoint) : m_client(client), m_endpoint(std::move(endpoint)) {}
        int transact(std::uint32_t addr, const Bytes& write, Bytes& read) override {
            JsonObj request;
            request.set("ev", "i2c_req").set("endpoint", m_endpoint).setNum("addr", addr).set("write", toHex(write));
            return m_client.rpc(request, read);
        }

      private:
        Client& m_client;
        std::string m_endpoint;
    };

    class RemoteSpi : public SpiDevice {
      public:
        RemoteSpi(Client& client, std::string endpoint) : m_client(client), m_endpoint(std::move(endpoint)) {}
        const std::string& endpoint() const { return m_endpoint; }
        int transfer(const Bytes& write, Bytes& read) override {
            JsonObj request;
            request.set("ev", "spi_req").set("endpoint", m_endpoint).set("write", toHex(write));
            return m_client.rpc(request, read);
        }

      private:
        Client& m_client;
        std::string m_endpoint;
    };

    //! Register block whose behaviour is supplied by this client
    class RemoteRegister : public RegisterDevice {
      public:
        RemoteRegister(Client& client, std::string endpoint) : m_client(client), m_endpoint(std::move(endpoint)) {}
        int read(std::uint64_t offset, unsigned width, std::uint64_t& value) override {
            JsonObj request;
            request.set("ev", "reg_req").set("endpoint", m_endpoint).set("access", "read");
            request.setHex("offset", offset).setNum("width", width);
            Bytes data(width, 0);
            const int status = m_client.rpc(request, data);
            value = 0;
            for (std::size_t i = 0; i < data.size() && i < 8; i++) {
                value |= static_cast<std::uint64_t>(data[i]) << (8 * i);
            }
            return status;
        }
        int write(std::uint64_t offset, unsigned width, std::uint64_t value) override {
            JsonObj request;
            request.set("ev", "reg_req").set("endpoint", m_endpoint).set("access", "write");
            request.setHex("offset", offset).setNum("width", width).setHex("value", value);
            Bytes none;
            return m_client.rpc(request, none);
        }

      private:
        Client& m_client;
        std::string m_endpoint;
    };

    void readLoop() {
        std::string buffer;
        char chunk[4096];
        while (true) {
            const ssize_t n = ::recv(m_fd, chunk, sizeof chunk, 0);
            if (n <= 0) {
                break;
            }
            buffer.append(chunk, static_cast<std::size_t>(n));
            std::size_t newline = 0;
            while ((newline = buffer.find('\n')) != std::string::npos) {
                const std::string line = buffer.substr(0, newline);
                buffer.erase(0, newline + 1);
                if (line.find_first_not_of(" \t\r") != std::string::npos) {
                    this->handle(line);
                }
            }
        }
        // Detach what this client was modelling, and nothing else: another device on the same
        // endpoint (a model at another I2C address) stays. Then release waiting transactions.
        for (const AttachedI2c& attached : m_i2c) {
            Bus::get().detachI2c(attached.endpoint, attached.addr, attached.remote.get());
        }
        for (const std::unique_ptr<RemoteSpi>& remote : m_spi) {
            Bus::get().detachSpi(remote->endpoint(), remote.get());
        }
        for (const AttachedPacket& attached : m_packet) {
            Bus::get().detachPacket(attached.endpoint, attached.addr, attached.remote.get());
        }
        // A register endpoint has one block: the one this client stood in for answers again
        for (auto it = m_register.rbegin(); it != m_register.rend(); ++it) {
            Bus::get().replaceRegister(it->endpoint, it->remote.get(), it->previous);
        }
        m_done.store(true);
        m_rpcCv.notify_all();
    }

    void handle(const std::string& line) {
        JsonObj request;
        JsonObj response;
        std::string error;
        if (!request.parse(line, error)) {
            response.setBool("ok", false).set("error", "bad request: " + error);
            this->send(response);
            return;
        }
        const std::string op = request.str("op");
        if (op == "reply") {
            std::lock_guard<std::mutex> lock(m_rpcMutex);
            const auto it = m_pending.find(static_cast<std::uint32_t>(request.num("rid")));
            if (it != m_pending.end()) {
                it->second.answered = true;
                it->second.status = static_cast<int>(request.num("status", 0));
                (void)fromHex(request.str("data"), it->second.data);
                m_rpcCv.notify_all();
            }
            return;  // replies are not acknowledged
        }
        if (request.has("id")) {
            response.setNum("id", request.num("id"));
        }
        bool ok = true;
        if (op == "status") {
            response = Control::get().status().toJson();
            if (request.has("id")) {
                response.setNum("id", request.num("id"));
            }
            std::string names;
            for (const std::string& name : Bus::get().endpoints()) {
                names += (names.empty() ? "" : ",") + name;
            }
            response.set("endpoints", names);
            response.setNum("links", LinkByteStream::started());
            response.setNum("links_up", LinkByteStream::connected());
            LinkByteStream::traffic(response);
        } else if (op == "bus_stats") {
            // Flat, as every bridge message is: bus.<endpoint>.<counter> and
            // bus.<endpoint>.at.<address>.<counter> (fprime_wsts.bridge.bus_traffic nests them)
            std::map<std::string, EndpointTraffic> traffic = Bus::get().traffic();
            for (const std::string& name : Bus::get().endpoints()) {
                (void)traffic[name];  // an endpoint nothing has used yet, with zeros
            }
            for (const auto& entry : traffic) {
                const std::string prefix = "bus." + entry.first + ".";
                const EndpointTraffic& t = entry.second;
                response.setNum(prefix + "transactions", static_cast<std::int64_t>(t.transactions));
                response.setNum(prefix + "to_device", static_cast<std::int64_t>(t.toDevice));
                response.setNum(prefix + "to_flight", static_cast<std::int64_t>(t.toFlight));
                response.setNum(prefix + "bytes_to_device", static_cast<std::int64_t>(t.bytesToDevice));
                response.setNum(prefix + "bytes_to_flight", static_cast<std::int64_t>(t.bytesToFlight));
                response.setNum(prefix + "errors", static_cast<std::int64_t>(t.errors));
                response.setNum(prefix + "unanswered", static_cast<std::int64_t>(t.unanswered));
                response.setNum(prefix + "fault_hits", static_cast<std::int64_t>(t.faultHits));
                response.setNum(prefix + "last_tick", static_cast<std::int64_t>(t.lastTick));
                for (const auto& at : t.byAddress) {
                    char address[24];
                    std::snprintf(address, sizeof(address), "0x%llx", static_cast<unsigned long long>(at.first));
                    const std::string key = prefix + "at." + address + ".";
                    response.setNum(key + "transactions", static_cast<std::int64_t>(at.second.transactions));
                    response.setNum(key + "errors", static_cast<std::int64_t>(at.second.errors));
                    response.setNum(key + "unanswered", static_cast<std::int64_t>(at.second.unanswered));
                }
            }
        } else if (op == "tick_times") {
            // Stepped time: how long ticks took in host time (fprime_wsts.bridge.tick_times nests it)
            Control::get().tickTimes().addTo(response);
        } else if (op == "subscribe") {
            m_subscribed.store(true);
        } else if (op == "attach") {
            ok = this->attach(request, error);
        } else if (op == "gpio_get") {
            const std::string endpoint = request.str("endpoint");
            if (Bus::get().known(endpoint)) {
                response.setBool("level", Bus::get().gpioOutput(endpoint));
            } else {
                ok = false;
                error = "unknown endpoint '" + endpoint + "'";
            }
        } else if (op == "wait_quiescent") {
            // Block until flight software has finished what it was given, or the time is up
            const std::int64_t timeout = std::max<std::int64_t>(0, std::min<std::int64_t>(request.num("timeout_ms", 1000), 60000));
            const bool quiet = Activity::get().waitQuiescent(std::chrono::milliseconds(timeout));
            response.setBool("quiescent", quiet).setBool("counting", Activity::get().counting());
            response.setNum("activity", static_cast<std::int64_t>(Activity::get().pending()));
            if (!quiet) {
                response.set("holding", Activity::get().holders());  // the queues with work outstanding
            }
        } else if (op == "advance") {
            ok = this->advance(request, response, error);
        } else {
            // In stepped time: between two ticks, and finished before the next one
            Control::ActionGate gate;
            // An action may run flight handlers on this thread (injected bytes, an interrupt)
            Activity::Scope actionWork;
            ok = Control::get().exec(request, error);
        }
        response.setBool("ok", ok);
        if (!ok) {
            response.set("error", error);
        }
        this->send(response);
    }

    //! Lockstep: the conductor of a system grants this element ticks and waits until they
    //! are taken and finished. `expect.<link>` says how many messages the link will have
    //! received before then. Answers with the status, and what the links sent and received.
    bool advance(const JsonObj& request, JsonObj& response, std::string& error) {
        Control& control = Control::get();
        if (!control.lockstep()) {
            error = "not in lockstep (set_stepped with lockstep: true)";
            return false;
        }
        const std::string prefix = "expect.";
        for (const std::string& key : request.keys()) {
            if (key.rfind(prefix, 0) != 0) {
                continue;
            }
            LinkEnd* link = LinkRegistry::find(key.substr(prefix.size()));
            if (link == nullptr) {
                error = "no link '" + key.substr(prefix.size()) + "' in this element";
                return false;
            }
            link->expect(request.unum(key));
        }
        const std::int64_t ticks = request.num("ticks", 1);
        if (ticks < 0 || ticks > 1000000000) {
            error = "ticks must be between 0 and 1000000000";
            return false;
        }
        if (ticks > 0 && !control.grant(static_cast<std::uint64_t>(ticks))) {
            error = "lockstep ended";
            return false;
        }
        const std::int64_t timeout = std::max<std::int64_t>(0, std::min<std::int64_t>(request.num("timeout_ms", 30000), 600000));
        const bool done = control.awaitGranted(std::chrono::milliseconds(timeout));
        response = control.status().toJson();
        if (request.has("id")) {
            response.setNum("id", request.num("id"));
        }
        response.setBool("done", done);
        LinkByteStream::traffic(response);
        return true;
    }

    bool attach(const JsonObj& request, std::string& error) {
        const std::string endpoint = request.str("endpoint");
        const std::string kind = request.str("kind");
        if (endpoint.empty()) {
            error = "missing endpoint";
            return false;
        }
        if (kind == "i2c") {
            const auto addr = static_cast<std::uint32_t>(request.num("addr"));
            m_i2c.push_back(AttachedI2c{endpoint, addr, std::unique_ptr<RemoteI2c>(new RemoteI2c(*this, endpoint))});
            Bus::get().attachI2c(endpoint, addr, m_i2c.back().remote.get());
        } else if (kind == "spi") {
            m_spi.emplace_back(new RemoteSpi(*this, endpoint));
            Bus::get().attachSpi(endpoint, m_spi.back().get());
        } else if (kind == "register") {
            std::unique_ptr<RemoteRegister> remote(new RemoteRegister(*this, endpoint));
            RegisterDevice* previous = Bus::get().attachRegister(endpoint, remote.get());
            m_register.push_back(AttachedRegister{endpoint, std::move(remote), previous});
            return true;
        } else if (kind == "packet") {
            std::uint32_t addr = PACKET_ANY_ADDRESS;
            if (request.str("addr") != "any") {
                const std::int64_t given = request.num("addr", -1);
                if (given < PACKET_FIRST_LOGICAL || given > 0xFF) {
                    error = "a packet node's addr is a logical address, 32 to 255, or \"any\"";
                    return false;
                }
                addr = static_cast<std::uint32_t>(given);
            }
            m_packet.push_back(AttachedPacket{endpoint, addr, std::unique_ptr<RemotePacket>(new RemotePacket(*this, endpoint))});
            Bus::get().attachPacket(endpoint, addr, m_packet.back().remote.get());
        } else {
            error = "attach supports kind i2c, spi, register or packet; byte stream and GPIO models use subscribe "
                    "plus inject_bytes / gpio_force";
            return false;
        }
        return true;
    }

    int m_fd;
    unsigned m_rpcTimeoutMs;
    std::thread m_thread;
    std::atomic<bool> m_done{false};
    std::atomic<bool> m_subscribed{false};
    std::mutex m_sendMutex;
    std::mutex m_rpcMutex;
    std::condition_variable m_rpcCv;
    std::map<std::uint32_t, Pending> m_pending;
    std::uint32_t m_nextRid = 1;
    struct AttachedI2c {
        std::string endpoint;
        std::uint32_t addr;
        std::unique_ptr<RemoteI2c> remote;
    };
    std::vector<AttachedI2c> m_i2c;
    std::vector<std::unique_ptr<RemoteSpi>> m_spi;
    struct AttachedRegister {
        std::string endpoint;
        std::unique_ptr<RemoteRegister> remote;
        RegisterDevice* previous;  //!< the block that was on the endpoint before
    };
    std::vector<AttachedRegister> m_register;
    struct AttachedPacket {
        std::string endpoint;
        std::uint32_t addr;
        std::unique_ptr<RemotePacket> remote;
    };
    std::vector<AttachedPacket> m_packet;
};

// ----------------------------------------------------------------------
// Bridge
// ----------------------------------------------------------------------

Bridge& Bridge::get() {
    // Never destroyed, as the Bus: a thread of the bridge may still be running when the process exits
    static Bridge* bridge = new Bridge;
    return *bridge;
}

bool Bridge::start(const std::string& address) {
    if (m_running.load() || address.empty() || address == "none") {
        return m_running.load();
    }
    if (address.rfind("unix:", 0) == 0) {
        m_unixPath = address.substr(5);
        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        if (m_unixPath.size() >= sizeof addr.sun_path) {
            std::fprintf(stderr, "[WSTS] bridge: socket path too long: %s\n", m_unixPath.c_str());
            return false;
        }
        std::strncpy(addr.sun_path, m_unixPath.c_str(), sizeof addr.sun_path - 1);
        ::unlink(m_unixPath.c_str());
        m_listenFd = ::socket(AF_UNIX, SOCK_STREAM, 0);
        if (m_listenFd < 0 || ::bind(m_listenFd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
            std::fprintf(stderr, "[WSTS] bridge: cannot bind %s: %s\n", address.c_str(), std::strerror(errno));
            return false;
        }
    } else if (address.rfind("tcp:", 0) == 0) {
        std::string rest = address.substr(4);
        std::string host = "127.0.0.1";
        const std::size_t colon = rest.rfind(':');
        if (colon != std::string::npos) {
            host = rest.substr(0, colon);
            rest = rest.substr(colon + 1);
        }
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(static_cast<std::uint16_t>(std::atoi(rest.c_str())));
        if (::inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
            std::fprintf(stderr, "[WSTS] bridge: bad host in %s\n", address.c_str());
            return false;
        }
        m_listenFd = ::socket(AF_INET, SOCK_STREAM, 0);
        const int one = 1;
        (void)::setsockopt(m_listenFd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        if (m_listenFd < 0 || ::bind(m_listenFd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
            std::fprintf(stderr, "[WSTS] bridge: cannot bind %s: %s\n", address.c_str(), std::strerror(errno));
            return false;
        }
        socklen_t len = sizeof addr;
        if (::getsockname(m_listenFd, reinterpret_cast<sockaddr*>(&addr), &len) == 0) {
            m_port = ntohs(addr.sin_port);
        }
    } else {
        std::fprintf(stderr, "[WSTS] bridge: unrecognized address '%s'\n", address.c_str());
        return false;
    }
    if (::listen(m_listenFd, 4) != 0) {
        std::fprintf(stderr, "[WSTS] bridge: listen failed: %s\n", std::strerror(errno));
        return false;
    }
    m_running.store(true);

    // Fan bus records and simulation events out to subscribed clients
    Control::get().setRecordTap([this](const JsonObj& line) {
        JsonObj out = line;
        if (!out.has("ev")) {
            out.set("ev", out.has("rec") ? "rec" : "action");
        }
        std::lock_guard<std::mutex> lock(m_mutex);
        for (const auto& client : m_clients) {
            if (client->subscribed() && !client->done()) {
                client->send(out);
            }
        }
    });
    Control::get().setEventTap([this](const SimEvent& event) {
        const JsonObj out = event.toJson();
        std::lock_guard<std::mutex> lock(m_mutex);
        for (const auto& client : m_clients) {
            if (client->subscribed() && !client->done()) {
                client->send(out);
            }
        }
    });

    m_acceptThread = std::thread([this]() { this->acceptLoop(); });
    std::fprintf(stderr, "[WSTS] bridge listening on %s\n", address.c_str());
    return true;
}

void Bridge::acceptLoop() {
    while (m_running.load()) {
        const int fd = ::accept(m_listenFd, nullptr, nullptr);
        if (fd < 0) {
            if (!m_running.load()) {
                break;
            }
            continue;
        }
        const int one = 1;
        (void)::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        this->reap();
        auto client = std::make_shared<Client>(fd, m_rpcTimeoutMs);
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_clients.push_back(client);
        }
        client->start();
    }
}

void Bridge::reap() {
    std::vector<std::shared_ptr<Client>> finished;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        for (auto it = m_clients.begin(); it != m_clients.end();) {
            if ((*it)->done()) {
                finished.push_back(*it);
                it = m_clients.erase(it);
            } else {
                ++it;
            }
        }
    }
    // Client destructors (thread joins) run here, outside the lock
}

void Bridge::stop() {
    if (!m_running.exchange(false)) {
        return;
    }
    Control::get().setRecordTap(nullptr);
    Control::get().setEventTap(nullptr);
    ::shutdown(m_listenFd, SHUT_RDWR);
    ::close(m_listenFd);
    if (m_acceptThread.joinable()) {
        m_acceptThread.join();
    }
    std::vector<std::shared_ptr<Client>> clients;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        clients.swap(m_clients);
    }
    for (const auto& client : clients) {
        client->shutdown();
    }
    clients.clear();
    if (!m_unixPath.empty()) {
        ::unlink(m_unixPath.c_str());
        m_unixPath.clear();
    }
    m_listenFd = -1;
    m_port = 0;
}

}  // namespace Wsts
