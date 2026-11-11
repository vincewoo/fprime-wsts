// ======================================================================
// \title  Link.cpp
// \brief  Links between simulated deployments (multi-element systems)
// ======================================================================
#include <Wsts/Core/Link.hpp>

#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace Wsts {

namespace {

const int POLL_MS = 100;                           //!< How often blocked calls look at the stop flag
const std::uint32_t MAX_MESSAGE = 16 * 1024 * 1024;  //!< Larger lengths mean a corrupted stream

std::atomic<std::uint32_t> s_started{0};
std::atomic<std::uint32_t> s_connected{0};

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

LinkByteStream::~LinkByteStream() {
    this->stop();
}

std::uint32_t LinkByteStream::started() {
    return s_started.load();
}

std::uint32_t LinkByteStream::connected() {
    return s_connected.load();
}

bool LinkByteStream::set(const std::string& key, const std::string& value) {
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

void LinkByteStream::start() {
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
    s_started++;
    m_thread = std::thread([this]() { this->run(); });
}

void LinkByteStream::stop() {
    if (!m_running.exchange(false)) {
        return;
    }
    if (m_thread.joinable()) {
        m_thread.join();
    }
    s_started--;
    if (m_listen) {
        (void)::unlink(m_address.c_str());
    }
}

void LinkByteStream::onBytes(const Bytes& tx, ByteSink& reply) {
    (void)reply;
    const std::uint32_t size = static_cast<std::uint32_t>(tx.size());
    const std::uint8_t header[4] = {
        static_cast<std::uint8_t>(size),
        static_cast<std::uint8_t>(size >> 8),
        static_cast<std::uint8_t>(size >> 16),
        static_cast<std::uint8_t>(size >> 24),
    };
    std::lock_guard<std::mutex> lock(m_sendMutex);
    // With no peer the bytes go nowhere, as on a cable with nothing at the far end
    if (m_fd < 0 || !writeFully(m_fd, header, sizeof(header)) || !writeFully(m_fd, tx.data(), tx.size())) {
        m_dropped++;
    }
}

int LinkByteStream::listenSocket() {
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

int LinkByteStream::connectSocket() {
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

bool LinkByteStream::readFully(int fd, std::uint8_t* data, std::size_t size) {
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

void LinkByteStream::serve(int fd) {
    {
        std::lock_guard<std::mutex> lock(m_sendMutex);
        m_fd = fd;
    }
    m_up.store(true);
    s_connected++;
    std::fprintf(stderr, "[WSTS] link %s: up\n", m_name.c_str());

    Bytes message;
    while (m_running.load()) {
        std::uint8_t header[4];
        if (!this->readFully(fd, header, sizeof(header))) {
            break;
        }
        const std::uint32_t size = static_cast<std::uint32_t>(header[0]) | (static_cast<std::uint32_t>(header[1]) << 8) |
                                   (static_cast<std::uint32_t>(header[2]) << 16) |
                                   (static_cast<std::uint32_t>(header[3]) << 24);
        if (size > MAX_MESSAGE) {
            break;
        }
        message.resize(size);
        if (size > 0 && !this->readFully(fd, message.data(), size)) {
            break;
        }
        Bus::get().byteStreamInject(m_endpoint, message, "link");
    }

    s_connected--;
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

void LinkByteStream::run() {
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
