// ======================================================================
// \title  LinkGpio.hpp
// \brief  GPIO links between simulated deployments (multi-element systems)
//
// A system of several compute elements runs as one process per element. A GPIO link joins a
// pin driven by one element's flight software to an input of another's: what flight software
// at end `a` writes arrives as a level at end `b`, as it would over a discrete wire between
// the two computers. Direction is a to b; the other way is a second link.
//
// The link is a device model behind the bus, like LinkByteStream: `gpioWrite` on the endpoint
// at end `a` reaches onWrite(), and what arrives at end `b` is applied with
// Bus::gpioSetInput(endpoint, level, "link"), so the surrogate bound there fires flight
// software's interrupt as for any device-driven input.
//
// Transport and lockstep semantics are LinkByteStream's (Link.hpp, Link.cpp) exactly: a Unix
// stream socket named <WSTS_LINK_DIR>/<link>.sock, one side listening and the other
// connecting, one message = 4 bytes of length + 8 bytes of Control::elapsedNs() + one level
// byte. In lockstep arrivals are held and delivered at the first tick later than the time
// they were sent at; sends made before the peer is connected wait for it, bounded like
// MAX_WAITING, rather than being lost.
// ======================================================================
#ifndef WSTS_CORE_LINK_GPIO_HPP
#define WSTS_CORE_LINK_GPIO_HPP

#include <atomic>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Link.hpp>

namespace Wsts {

//! One end of a GPIO link to another simulated deployment.
//! Parameters: endpoint, link (name), role ("listen" or "connect"), address (socket path;
//! default <WSTS_LINK_DIR>/<link>.sock).
class LinkGpio : public Model, public GpioDevice, public Inbound {
  public:
    LinkGpio() = default;
    ~LinkGpio() override;
    LinkGpio(const LinkGpio&) = delete;
    LinkGpio& operator=(const LinkGpio&) = delete;

    bool set(const std::string& key, const std::string& value) override;
    //! Bus side: flight software wrote the pin at end `a`. Sends one message.
    void onWrite(bool level) override;
    std::string deliverDue(std::uint64_t beforeNs, std::chrono::steady_clock::time_point limit) override;

    //! Start connecting. Without an address (WSTS_LINK_DIR unset and none given) the link
    //! stays down, so that an element can still be run on its own.
    void start();
    void stop();

    //! The peer is connected
    bool up() const { return m_up.load(); }
    //! Levels discarded because the peer was not connected
    std::uint64_t dropped() const { return m_dropped.load(); }

    //! Levels sent to the peer (or waiting for it, in lockstep), and received from it
    std::uint64_t sent() const { return m_sent.load(); }
    std::uint64_t received() const;
    //! Lockstep: this many messages will have arrived before the next tick takes what is due
    void expect(std::uint64_t received);

  private:
    void run();
    int listenSocket();
    int connectSocket();
    //! Receive from the peer until it disconnects or the link is stopped
    void serve(int fd);
    bool readFully(int fd, std::uint8_t* data, std::size_t size);
    //! Write one message (m_sendMutex held). False if the peer is not there.
    bool writeMessage(std::uint64_t timeNs, bool level);

    struct Message {
        std::uint64_t timeNs;  //!< simulated time it was sent at
        bool level;
    };

    std::string m_endpoint;
    std::string m_name;
    std::string m_address;
    bool m_listen = false;

    std::thread m_thread;
    std::atomic<bool> m_running{false};
    std::atomic<bool> m_up{false};
    std::atomic<std::uint64_t> m_dropped{0};
    std::atomic<std::uint64_t> m_sent{0};
    std::mutex m_sendMutex;  //!< m_fd, writes to it, and m_outbox
    int m_fd = -1;
    std::deque<Message> m_outbox;  //!< lockstep: sent before the peer was connected

    mutable std::mutex m_inboxMutex;  //!< the three below
    std::condition_variable m_arrived;
    std::deque<Message> m_inbox;  //!< lockstep: arrived, not yet due
    std::uint64_t m_received = 0;
    std::uint64_t m_expected = 0;
};

}  // namespace Wsts

#endif
