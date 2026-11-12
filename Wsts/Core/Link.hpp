// ======================================================================
// \title  Link.hpp
// \brief  Links between simulated deployments (multi-element systems)
//
// A system of several compute elements runs as one process per element. A link joins a
// byte stream endpoint of one element to an endpoint of another: what flight software
// sends on one side arrives on the other, as it would over a UART or a network connection
// between the two computers.
//
// The link is a device model, so it sits behind the bus like any other: faults on either
// endpoint act on the traffic, and each side's recorder sees it.
//
// Transport: a Unix stream socket named <WSTS_LINK_DIR>/<link>.sock, which `fprime-wsts run`
// sets for every element of a system. One side listens and the other connects; both keep
// trying, so elements may start in any order and may be restarted. Each send by flight
// software travels as one length-prefixed message and is delivered whole.
// ======================================================================
#ifndef WSTS_CORE_LINK_HPP
#define WSTS_CORE_LINK_HPP

#include <atomic>
#include <mutex>
#include <string>
#include <thread>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>

namespace Wsts {

//! One end of a byte stream link to another simulated deployment.
//! Parameters: endpoint, link (name), role ("listen" or "connect"), address (socket path;
//! default <WSTS_LINK_DIR>/<link>.sock).
class LinkByteStream : public Model, public ByteStreamDevice {
  public:
    LinkByteStream() = default;
    ~LinkByteStream() override;
    LinkByteStream(const LinkByteStream&) = delete;
    LinkByteStream& operator=(const LinkByteStream&) = delete;

    bool set(const std::string& key, const std::string& value) override;
    void onBytes(const Bytes& tx, ByteSink& reply) override;
    bool peer() const override { return true; }

    //! Start connecting. Without an address (WSTS_LINK_DIR unset and none given) the link
    //! stays down, so that an element can still be run on its own.
    void start();
    void stop();

    //! The peer is connected
    bool up() const { return m_up.load(); }
    //! Messages discarded because the peer was not connected
    std::uint64_t dropped() const { return m_dropped.load(); }

    //! Links started in this process, and how many of them are up (bridge status)
    static std::uint32_t started();
    static std::uint32_t connected();

  private:
    void run();
    int listenSocket();
    int connectSocket();
    //! Receive from the peer until it disconnects or the link is stopped
    void serve(int fd);
    bool readFully(int fd, std::uint8_t* data, std::size_t size);

    std::string m_endpoint;
    std::string m_name;
    std::string m_address;
    bool m_listen = false;

    std::thread m_thread;
    std::atomic<bool> m_running{false};
    std::atomic<bool> m_up{false};
    std::atomic<std::uint64_t> m_dropped{0};
    std::mutex m_sendMutex;  //!< m_fd, and writes to it
    int m_fd = -1;
};

}  // namespace Wsts

#endif
