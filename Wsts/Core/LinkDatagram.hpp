// ======================================================================
// \title  LinkDatagram.hpp
// \brief  Datagram links between simulated deployments
//
// A datagram link joins a byte stream endpoint of one element to one of another over a
// connectionless Unix datagram socket: no listen, no connect, no connection state. Each end
// binds a socket of its own, <WSTS_LINK_DIR>/<link>.a.sock at end `a` and
// <WSTS_LINK_DIR>/<link>.b.sock at end `b`, and sends to the peer's path with sendto().
//
// One message from flight software is one datagram: the 4-byte length + 8-byte
// Control::elapsedNs() header and the bytes in a single sendto(). A datagram arrives whole
// or not at all. In real time a send while the peer has no socket goes nowhere and counts in
// dropped(), as on a cable with nothing at the far end. In lockstep it waits in the outbox
// and goes as soon as the peer's socket appears, so that two runs record the same bytes.
//
// Like a stream link, this is a device model: faults on either endpoint act on the traffic
// and each side's recorder sees it. Received messages are held in lockstep and delivered by
// deliverDue() before the tick's time, exactly as LinkByteStream does.
// ======================================================================
#ifndef WSTS_CORE_LINKDATAGRAM_HPP
#define WSTS_CORE_LINKDATAGRAM_HPP

#include <atomic>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/LinkRegistry.hpp>

namespace Wsts {

//! One end of a connectionless datagram link to another simulated deployment.
//! Parameters: endpoint, link (name), role ("listen" at end a, "connect" at end b; it says
//! which of the two socket files this end binds), address (end a's socket path; default
//! <WSTS_LINK_DIR>/<link>.a.sock), address_b (end b's; default <link>.b.sock). Both ends
//! take the same two paths: the role decides which one this end binds and which it sends to.
class LinkDatagram : public Model, public ByteStreamDevice, public Inbound, public LinkEnd {
  public:
    LinkDatagram() = default;
    ~LinkDatagram() override;
    LinkDatagram(const LinkDatagram&) = delete;
    LinkDatagram& operator=(const LinkDatagram&) = delete;

    bool set(const std::string& key, const std::string& value) override;
    void onBytes(const Bytes& tx, ByteSink& reply) override;
    bool peer() const override { return true; }

    //! Bind this end's socket. Without WSTS_LINK_DIR (and none given) the link stays down,
    //! so that an element can still be run on its own.
    void start();
    void stop();

    //! Messages discarded because the peer's socket was not there (real time only)
    std::uint64_t dropped() const { return m_dropped.load(); }

    //! Messages sent to the peer (or waiting for it, in lockstep), and received from it
    std::uint64_t sent() const override { return m_sent.load(); }
    std::uint64_t received() const override;
    //! Lockstep: this many messages will have arrived before the next tick takes what is due
    void expect(std::uint64_t received) override;
    std::string deliverDue(std::uint64_t beforeNs, std::chrono::steady_clock::time_point limit) override;

    //! A datagram link has no connection, so it is never up (bridge status)
    bool isUp() const override { return false; }

    const std::string& name() const override { return m_name; }

  private:
    void run();
    //! Bind this end's socket; -1 if it cannot be bound
    int bindSocket();
    //! Send one datagram (m_sendMutex held). False if the peer's socket is not there.
    bool sendDatagram(const std::string& path, std::uint64_t timeNs, const Bytes& data);
    //! Send what waited for the peer's socket, in order (m_sendMutex held)
    void flushOutbox();

    struct Message {
        std::uint64_t timeNs;  //!< simulated time it was sent at
        Bytes data;
    };

    std::string m_endpoint;
    std::string m_name;
    std::string m_addressA;      //!< end a's socket file
    std::string m_addressB;      //!< end b's socket file
    bool m_endA = true;          //!< this end binds .a.sock and sends to .b.sock
    std::string m_address;       //!< this end's socket file, from the two above and the role
    std::string m_peerAddress;   //!< the peer's socket file

    std::thread m_thread;
    std::atomic<bool> m_running{false};
    std::atomic<std::uint64_t> m_dropped{0};
    std::atomic<std::uint64_t> m_sent{0};
    std::mutex m_sendMutex;  //!< m_fd, sends to it, and m_outbox
    int m_fd = -1;
    std::deque<Message> m_outbox;  //!< lockstep: sent before the peer's socket existed

    mutable std::mutex m_inboxMutex;  //!< the three below
    std::condition_variable m_arrived;
    std::deque<Message> m_inbox;   //!< lockstep: arrived, not yet due
    std::uint64_t m_received = 0;
    std::uint64_t m_expected = 0;
};

}  // namespace Wsts

#endif
