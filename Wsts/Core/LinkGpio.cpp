// ======================================================================
// \title  LinkGpio.cpp
// \brief  GPIO links between simulated deployments (multi-element systems)
// ======================================================================
#include <Wsts/Core/LinkGpio.hpp>

#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <utility>
#include <vector>

namespace Wsts {

namespace {

const int POLL_MS = 100;                           //!< How often blocked calls look at the stop flag
const std::uint32_t MAX_MESSAGE = 16 * 1024 * 1024;  //!< Larger lengths mean a corrupted stream
const std::size_t MAX_WAITING = 65536;               //!< Lockstep: messages kept for a peer not yet connected
const std::size_t HEADER = 12;                       //!< Length (4 bytes), then the time it was sent at (8)
const std::uint32_t LEVEL_MESSAGE = 1;               //!< The one level byte each message carries

bool unixAddress(const std::string& path, sockaddr_un& address) {
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (path.size() >= sizeof(address.sun_path)) {
        return false;
    }
    (void)std::memcpy(address.sun_path, path.c_str(), path.size());
    return true;
}

bool writeFully(int fd, const std::uint8_t* data, std::size_t size) {
    std::size_t done = 0;
    while (done < size) {
        const ssize_t sent = ::send(fd, data + done, size - done, MSG_NOSIGNAL);
        if (sent <= 0) {
            return false;
        }
        done += static_cast<std::size_t>(sent);
    }
    return true;
}

}  // namespace

LinkGpio::~LinkGpio() {
    this->stop();
}

bool LinkGpio::set(const std::string& key, const std::string& value) {
    if (m_running.load()) {
        return false;  // the link is configured before it starts
    }
    if (key == "endpoint") {
        m_endpoint = value;
    } else if (key == "link") {
        m_name = value;
    } else if (key == "address") {
        m_address = value;
    } else if (key == "role" && (value == "listen" || value == "connect")) {
        m_listen = (value == "listen");
    } else {
        return false;
    }
    return true;
}

std::uint64_t LinkGpio::received() const {
    std::lock_guard<std::mutex> lock(m_inboxMutex);
    return m_received;
}

void LinkGpio::expect(std::uint64_t received) {
    std::lock_guard<std::mutex> lock(m_inboxMutex);
    m_expected = received;
}

std::string LinkGpio::deliverDue(std::uint64_t beforeNs, std::chrono::steady_clock::time_point limit) {
    std::vector<bool> due;
    std::string late;
    {
        std::unique_lock<std::mutex> lock(m_inboxMutex);
        if (!m_arrived.wait_until(lock, limit, [this]() { return m_received >= m_expected; })) {
            late = "link " + m_name + ": " + std::to_string(m_received) + " of the " + std::to_string(m_expected) +
                   " messages the other element sent had arrived";
        }
        // In the order they arrived, which is the order they were sent in, and so of their times
        while (!m_inbox.empty() && m_inbox.front().timeNs < beforeNs) {
            due.push_back(m_inbox.front().level);
            m_inbox.pop_front();
        }
    }
    for (bool level : due) {
        Bus::get().gpioSetInput(m_endpoint, level, "link");
    }
    return late;
}

void LinkGpio::start() {
    if (m_running.load()) {
        return;
    }
    if (m_address.empty()) {
        const char* directory = std::getenv("WSTS_LINK_DIR");
        if (directory == nullptr || directory[0] == '\0') {
            std::fprintf(stderr,
                         "[WSTS] link %s: not connected, WSTS_LINK_DIR is not set "
                         "(start the whole system with `fprime-wsts run`)\n",
                         m_name.c_str());
            return;
        }
        m_address = std::string(directory) + "/" + m_name + ".sock";
    }
    sockaddr_un address;
    if (!unixAddress(m_address, address)) {
        std::fprintf(stderr, "[WSTS] link %s: socket path is too long: %s\n", m_name.c_str(), m_address.c_str());
        return;
    }
    m_running.store(true);
    // The shared registry is what the bridge status reports as `link.<name>.sent` and
    // `link.<name>.received` (LinkRegistry::traffic()), for every kind of link.
    LinkRegistry::add(this);
    Control::get().addInbound(this);
    m_thread = std::thread([this]() { this->run(); });
}

void LinkGpio::stop() {
    if (!m_running.exchange(false)) {
        return;
    }
    if (m_thread.joinable()) {
        m_thread.join();
    }
    Control::get().removeInbound(this);
    LinkRegistry::remove(this);
    if (m_listen) {
        (void)::unlink(m_address.c_str());
    }
}

bool LinkGpio::writeMessage(std::uint64_t timeNs, bool level) {
    if (m_fd < 0) {
        return false;
    }
    std::uint8_t header[HEADER];
    const std::uint32_t size = LEVEL_MESSAGE;
    for (std::size_t i = 0; i < 4; i++) {
        header[i] = static_cast<std::uint8_t>(size >> (8 * i));
    }
    for (std::size_t i = 0; i < 8; i++) {
        header[4 + i] = static_cast<std::uint8_t>(timeNs >> (8 * i));
    }
    const std::uint8_t payload = level ? 1 : 0;
    return writeFully(m_fd, header, sizeof(header)) && writeFully(m_fd, &payload, 1);
}

void LinkGpio::onWrite(bool level) {
    const std::uint64_t now = Control::get().elapsedNs();
    std::lock_guard<std::mutex> lock(m_sendMutex);
    if (this->writeMessage(now, level)) {
        m_sent++;
        return;
    }
    if (Control::get().lockstep() && m_running.load() && m_outbox.size() < MAX_WAITING) {
        // Lockstep: whether the peer is there yet must not decide what it gets. It is
        // connected before the conductor takes the first instant.
        m_outbox.push_back(Message{now, level});
        m_sent++;
        return;
    }
    // With no peer the level goes nowhere, as on a wire with nothing at the far end
    m_dropped++;
}

int LinkGpio::listenSocket() {
    sockaddr_un address;
    (void)unixAddress(m_address, address);
    const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    (void)::unlink(m_address.c_str());
    if (::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 || ::listen(fd, 1) != 0) {
        std::fprintf(stderr, "[WSTS] link %s: cannot listen on %s: %s\n", m_name.c_str(), m_address.c_str(),
                     std::strerror(errno));
        (void)::close(fd);
        return -1;
    }
    return fd;
}

int LinkGpio::connectSocket() {
    sockaddr_un address;
    (void)unixAddress(m_address, address);
    const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        (void)::close(fd);
        return -1;
    }
    return fd;
}

bool LinkGpio::readFully(int fd, std::uint8_t* data, std::size_t size) {
    std::size_t done = 0;
    while (done < size) {
        pollfd waiting{fd, POLLIN, 0};
        const int ready = ::poll(&waiting, 1, POLL_MS);
        if (!m_running.load()) {
            return false;
        }
        if (ready == 0 || (ready < 0 && errno == EINTR)) {
            continue;
        }
        if (ready < 0) {
            return false;
        }
        const ssize_t got = ::recv(fd, data + done, size - done, 0);
        if (got == 0 || (got < 0 && errno != EINTR)) {
            return false;
        }
        if (got > 0) {
            done += static_cast<std::size_t>(got);
        }
    }
    return true;
}

void LinkGpio::serve(int fd) {
    {
        std::lock_guard<std::mutex> lock(m_sendMutex);
        m_fd = fd;
        // What waited for the peer goes first, in order
        while (!m_outbox.empty() && this->writeMessage(m_outbox.front().timeNs, m_outbox.front().level)) {
            m_outbox.pop_front();
        }
    }
    m_up.store(true);
    std::fprintf(stderr, "[WSTS] link %s: up\n", m_name.c_str());

    while (m_running.load()) {
        std::uint8_t header[HEADER];
        if (!this->readFully(fd, header, sizeof(header))) {
            break;
        }
        std::uint32_t size = 0;
        for (std::size_t i = 0; i < 4; i++) {
            size |= static_cast<std::uint32_t>(header[i]) << (8 * i);
        }
        std::uint64_t timeNs = 0;
        for (std::size_t i = 0; i < 8; i++) {
            timeNs |= static_cast<std::uint64_t>(header[4 + i]) << (8 * i);
        }
        if (size != LEVEL_MESSAGE) {
            break;
        }
        std::uint8_t payload = 0;
        if (!this->readFully(fd, &payload, 1)) {
            break;
        }
        const Message message{timeNs, payload != 0};
        bool hold = false;
        {
            std::lock_guard<std::mutex> lock(m_inboxMutex);
            m_received++;
            // Lockstep: held for the tick it belongs to. Decided under the lock, so that
            // the end of lockstep, which delivers what is held, misses nothing.
            hold = Control::get().lockstep();
            if (hold) {
                m_inbox.push_back(message);
            }
        }
        m_arrived.notify_all();
        if (!hold) {
            Bus::get().gpioSetInput(m_endpoint, message.level, "link");
        }
    }

    m_up.store(false);
    {
        std::lock_guard<std::mutex> lock(m_sendMutex);
        m_fd = -1;
    }
    (void)::close(fd);
    if (m_running.load()) {
        std::fprintf(stderr, "[WSTS] link %s: down\n", m_name.c_str());
    }
}

void LinkGpio::run() {
    int listener = -1;
    while (m_running.load()) {
        int fd = -1;
        if (m_listen) {
            if (listener < 0) {
                listener = this->listenSocket();
            }
            if (listener >= 0) {
                pollfd waiting{listener, POLLIN, 0};
                if (::poll(&waiting, 1, POLL_MS) > 0) {
                    fd = ::accept(listener, nullptr, nullptr);
                }
                if (fd < 0) {
                    continue;
                }
            }
        } else {
            fd = this->connectSocket();
        }
        if (fd < 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(POLL_MS));
            continue;
        }
        this->serve(fd);
    }
    if (listener >= 0) {
        (void)::close(listener);
    }
}

}  // namespace Wsts
