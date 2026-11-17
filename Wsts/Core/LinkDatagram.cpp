// ======================================================================
// \title  LinkDatagram.cpp
// \brief  Datagram links between simulated deployments
// ======================================================================
#include <Wsts/Core/LinkDatagram.hpp>

#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace Wsts {

namespace {

const int POLL_MS = 100;                             //!< How often blocked calls look at the stop flag
const std::uint32_t MAX_MESSAGE = 16 * 1024 * 1024;  //!< Larger lengths mean a corrupted datagram
const std::size_t MAX_WAITING = 65536;               //!< Lockstep: messages kept for a peer not yet bound
const std::size_t HEADER = 12;                       //!< Length (4 bytes), then the time it was sent at (8)

bool unixAddress(const std::string& path, sockaddr_un& address) {
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (path.size() >= sizeof(address.sun_path)) {
        return false;
    }
    (void)std::memcpy(address.sun_path, path.c_str(), path.size());
    return true;
}

}  // namespace

LinkDatagram::~LinkDatagram() {
    this->stop();
}

std::uint64_t LinkDatagram::received() const {
    std::lock_guard<std::mutex> lock(m_inboxMutex);
    return m_received;
}

void LinkDatagram::expect(std::uint64_t received) {
    std::lock_guard<std::mutex> lock(m_inboxMutex);
    m_expected = received;
}

std::string LinkDatagram::deliverDue(std::uint64_t beforeNs, std::chrono::steady_clock::time_point limit) {
    std::vector<Bytes> due;
    std::string late;
    {
        std::unique_lock<std::mutex> lock(m_inboxMutex);
        if (!m_arrived.wait_until(lock, limit, [this]() { return m_received >= m_expected; })) {
            late = "link " + m_name + ": " + std::to_string(m_received) + " of the " + std::to_string(m_expected) +
                   " messages the other element sent had arrived";
        }
        // In the order they arrived, which is the order they were sent in, and so of their times
        while (!m_inbox.empty() && m_inbox.front().timeNs < beforeNs) {
            due.push_back(std::move(m_inbox.front().data));
            m_inbox.pop_front();
        }
    }
    for (const Bytes& data : due) {
        Bus::get().byteStreamInject(m_endpoint, data, "link");
    }
    return late;
}

bool LinkDatagram::set(const std::string& key, const std::string& value) {
    if (m_running.load()) {
        return false;  // the link is configured before it starts
    }
    if (key == "endpoint") {
        m_endpoint = value;
    } else if (key == "link") {
        m_name = value;
    } else if (key == "role") {
        // Direction is only which socket file each end binds: the transport is symmetric
        if (value == "listen") {
            m_endA = true;
        } else if (value == "connect") {
            m_endA = false;
        } else {
            return false;
        }
    } else if (key == "address") {
        m_addressA = value;
    } else if (key == "address_b") {
        m_addressB = value;
    } else {
        return false;
    }
    return true;
}

void LinkDatagram::start() {
    if (m_running.load()) {
        return;
    }
    if (m_addressA.empty() || m_addressB.empty()) {
        const char* directory = std::getenv("WSTS_LINK_DIR");
        if (directory == nullptr || directory[0] == '\0') {
            std::fprintf(stderr,
                         "[WSTS] link %s: not connected, WSTS_LINK_DIR is not set "
                         "(start the whole system with `fprime-wsts run`)\n",
                         m_name.c_str());
            return;
        }
        const std::string base = std::string(directory) + "/" + m_name;
        if (m_addressA.empty()) {
            m_addressA = base + ".a.sock";
        }
        if (m_addressB.empty()) {
            m_addressB = base + ".b.sock";
        }
    }
    // Each end binds its own file and sends to the other's: no listen, no connect
    m_address = m_endA ? m_addressA : m_addressB;
    m_peerAddress = m_endA ? m_addressB : m_addressA;
    sockaddr_un own;
    if (!unixAddress(m_address, own)) {
        std::fprintf(stderr, "[WSTS] link %s: socket path is too long: %s\n", m_name.c_str(), m_address.c_str());
        return;
    }
    sockaddr_un peer;
    if (!unixAddress(m_peerAddress, peer)) {
        std::fprintf(stderr, "[WSTS] link %s: socket path is too long: %s\n", m_name.c_str(), m_peerAddress.c_str());
        return;
    }
    // Blocking sends, as on a stream link: a peer that is not reading applies backpressure
    // rather than losing messages the wire would deliver. Receives poll (MSG_DONTWAIT).
    const int fd = ::socket(AF_UNIX, SOCK_DGRAM, 0);
    if (fd < 0) {
        std::fprintf(stderr, "[WSTS] link %s: cannot take a datagram socket: %s\n", m_name.c_str(), std::strerror(errno));
        return;
    }
    // A file left by a run that did not clean up would keep this one from binding
    (void)::unlink(m_address.c_str());
    if (::bind(fd, reinterpret_cast<sockaddr*>(&own), sizeof(own)) != 0) {
        std::fprintf(stderr, "[WSTS] link %s: cannot bind %s: %s\n", m_name.c_str(), m_address.c_str(),
                     std::strerror(errno));
        (void)::close(fd);
        return;
    }
    {
        std::lock_guard<std::mutex> lock(m_sendMutex);
        m_fd = fd;
    }
    m_running.store(true);
    LinkRegistry::add(this);
    Control::get().addInbound(this);
    std::fprintf(stderr, "[WSTS] link %s: datagram %s (peer %s)\n", m_name.c_str(), m_address.c_str(),
                 m_peerAddress.c_str());
    m_thread = std::thread([this]() { this->run(); });
}

void LinkDatagram::stop() {
    if (!m_running.exchange(false)) {
        return;
    }
    if (m_thread.joinable()) {
        m_thread.join();
    }
    Control::get().removeInbound(this);
    LinkRegistry::remove(this);
    {
        std::lock_guard<std::mutex> lock(m_sendMutex);
        if (m_fd >= 0) {
            (void)::close(m_fd);
            m_fd = -1;
        }
        m_outbox.clear();
    }
    (void)::unlink(m_address.c_str());
}

bool LinkDatagram::sendDatagram(const std::string& path, std::uint64_t timeNs, const Bytes& data) {
    if (m_fd < 0) {
        return false;
    }
    sockaddr_un peer;
    if (!unixAddress(path, peer)) {
        return false;
    }
    std::vector<std::uint8_t> datagram(HEADER + data.size());
    const std::uint32_t size = static_cast<std::uint32_t>(data.size());
    for (std::size_t i = 0; i < 4; i++) {
        datagram[i] = static_cast<std::uint8_t>(size >> (8 * i));
    }
    for (std::size_t i = 0; i < 8; i++) {
        datagram[4 + i] = static_cast<std::uint8_t>(timeNs >> (8 * i));
    }
    if (!data.empty()) {
        (void)std::memcpy(datagram.data() + HEADER, data.data(), data.size());
    }
    // One datagram for one message: the peer reads it whole or not at all
    const ssize_t sent = ::sendto(m_fd, datagram.data(), datagram.size(), MSG_NOSIGNAL,
                                  reinterpret_cast<const sockaddr*>(&peer), sizeof(peer));
    return sent == static_cast<ssize_t>(datagram.size());
}

void LinkDatagram::flushOutbox() {
    std::lock_guard<std::mutex> lock(m_sendMutex);
    while (!m_outbox.empty()) {
        if (!this->sendDatagram(m_peerAddress, m_outbox.front().timeNs, m_outbox.front().data)) {
            return;  // the peer's socket is still not there
        }
        m_outbox.pop_front();
    }
}

void LinkDatagram::onBytes(const Bytes& tx, ByteSink& reply) {
    (void)reply;
    const std::uint64_t now = Control::get().elapsedNs();
    std::lock_guard<std::mutex> lock(m_sendMutex);
    // An older message still waits for the peer's socket: this one goes behind it, so that
    // the order flight software sent them in is the order the peer reads them in
    if (m_outbox.empty() && this->sendDatagram(m_peerAddress, now, tx)) {
        m_sent++;
        return;
    }
    if (Control::get().lockstep() && m_running.load() && m_outbox.size() < MAX_WAITING) {
        // Lockstep: whether the peer has bound its socket yet must not decide what it gets.
        // It is there before the conductor takes the first instant.
        m_outbox.push_back(Message{now, tx});
        m_sent++;
        return;
    }
    // Real time: there is no connection to wait for, and no retry. The bytes go nowhere,
    // as on a datagram socket with nothing bound at the far end.
    m_dropped++;
}

void LinkDatagram::run() {
    // One buffer of the largest message a datagram may hold: each read takes one datagram
    // whole (MSG_TRUNC would tell of a larger one, and its length then would not match)
    std::vector<std::uint8_t> buffer(HEADER + MAX_MESSAGE);
    int fd = -1;
    {
        std::lock_guard<std::mutex> lock(m_sendMutex);
        fd = m_fd;
    }
    while (m_running.load()) {
        // Only lockstep ever holds messages (real time drops a send the peer cannot take):
        // they go the moment the peer's socket is there
        this->flushOutbox();
        pollfd waiting{fd, POLLIN, 0};
        const int ready = ::poll(&waiting, 1, POLL_MS);
        if (!m_running.load()) {
            break;
        }
        if (ready <= 0) {
            continue;
        }
        const ssize_t got = ::recvfrom(fd, buffer.data(), buffer.size(), MSG_DONTWAIT, nullptr, nullptr);
        if (got < 0) {
            if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
                continue;
            }
            std::fprintf(stderr, "[WSTS] link %s: cannot receive: %s\n", m_name.c_str(), std::strerror(errno));
            continue;
        }
        // A datagram is one message, whole: anything else is not one this link sent
        if (static_cast<std::size_t>(got) < HEADER) {
            std::fprintf(stderr, "[WSTS] link %s: datagram of %zd bytes is shorter than its header\n", m_name.c_str(), got);
            continue;
        }
        std::uint32_t size = 0;
        for (std::size_t i = 0; i < 4; i++) {
            size |= static_cast<std::uint32_t>(buffer[i]) << (8 * i);
        }
        std::uint64_t timeNs = 0;
        for (std::size_t i = 0; i < 8; i++) {
            timeNs |= static_cast<std::uint64_t>(buffer[4 + i]) << (8 * i);
        }
        if (size > MAX_MESSAGE || HEADER + size != static_cast<std::size_t>(got)) {
            std::fprintf(stderr, "[WSTS] link %s: datagram of %zd bytes holds a message of %u\n", m_name.c_str(), got,
                         size);
            continue;
        }
        Message message{timeNs, Bytes(buffer.begin() + HEADER, buffer.begin() + HEADER + size)};
        bool hold = false;
        {
            std::lock_guard<std::mutex> lock(m_inboxMutex);
            m_received++;
            // Lockstep: held for the tick it belongs to. Decided under the lock, so that
            // the end of lockstep, which delivers what is held, misses nothing.
            hold = Control::get().lockstep();
            if (hold) {
                m_inbox.push_back(std::move(message));
            }
        }
        m_arrived.notify_all();
        if (!hold) {
            Bus::get().byteStreamInject(m_endpoint, message.data, "link");
        }
    }
}

}  // namespace Wsts
