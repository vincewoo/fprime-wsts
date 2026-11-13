// ======================================================================
// \title  Bridge.cpp
// \brief  Socket bridge implementation
// ======================================================================
#include <Wsts/Core/Bridge.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <map>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Link.hpp>

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

  private:
    struct Pending {
        bool answered = false;
        int status = 0;
        Bytes data;
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
        // Detach anything this client was modelling, then release waiting transactions
        for (const std::string& endpoint : m_attached) {
            Bus::get().detach(endpoint);
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
        } else {
            ok = Control::get().exec(request, error);
        }
        response.setBool("ok", ok);
        if (!ok) {
            response.set("error", error);
        }
        this->send(response);
    }

    bool attach(const JsonObj& request, std::string& error) {
        const std::string endpoint = request.str("endpoint");
        const std::string kind = request.str("kind");
        if (endpoint.empty()) {
            error = "missing endpoint";
            return false;
        }
        if (kind == "i2c") {
            m_i2c.emplace_back(new RemoteI2c(*this, endpoint));
            Bus::get().attachI2c(endpoint, static_cast<std::uint32_t>(request.num("addr")), m_i2c.back().get());
        } else if (kind == "spi") {
            m_spi.emplace_back(new RemoteSpi(*this, endpoint));
            Bus::get().attachSpi(endpoint, m_spi.back().get());
        } else if (kind == "register") {
            std::unique_ptr<RemoteRegister> remote(new RemoteRegister(*this, endpoint));
            RegisterDevice* previous = Bus::get().attachRegister(endpoint, remote.get());
            m_register.push_back(AttachedRegister{endpoint, std::move(remote), previous});
            return true;
        } else {
            error = "attach supports kind i2c, spi or register; byte stream and GPIO models use subscribe plus "
                    "inject_bytes / gpio_force";
            return false;
        }
        m_attached.push_back(endpoint);
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
    std::vector<std::unique_ptr<RemoteI2c>> m_i2c;
    std::vector<std::unique_ptr<RemoteSpi>> m_spi;
    struct AttachedRegister {
        std::string endpoint;
        std::unique_ptr<RemoteRegister> remote;
        RegisterDevice* previous;  //!< the block that was on the endpoint before
    };
    std::vector<AttachedRegister> m_register;
    std::vector<std::string> m_attached;
};

// ----------------------------------------------------------------------
// Bridge
// ----------------------------------------------------------------------

Bridge& Bridge::get() {
    static Bridge bridge;
    return bridge;
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
