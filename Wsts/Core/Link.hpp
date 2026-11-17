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
// software travels as one message and is delivered whole: a length, the simulated time it
// was sent at (Control::elapsedNs()), the bytes.
//
// Lockstep (docs/stepped-time.md, phase 4): what arrives is held, and delivered at the start
// of the first tick of this element later than the time it was sent at. What flight
// software sends before the peer is connected waits for it rather than being lost. Each end
// counts what it sent and received; the conductor tells the receiving end how many messages
// will have arrived before a tick (expect()), so that a tick never starts before them.
// ======================================================================
#ifndef WSTS_CORE_LINK_HPP
#define WSTS_CORE_LINK_HPP

#include <atomic>
#include <condition_variable>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>

namespace Wsts {

//! Every link started in this process, whichever kind it carries. The bridge status and the
//! lockstep conductor see all of them here: started(), connected(), traffic() and expect().
//! A link registers when it starts and removes itself when it stops, so the counts cover
//! bytestream and GPIO links alike (a datagram link will land here too).
class LinkRegistry {
  public:
    enum class Kind { BYTESTREAM, GPIO };

    struct Entry {
        Kind kind;
        std::string name;
        const void* owner;  //!< the link that registered, for removal
        std::function<std::uint64_t()> sent;
        std::function<std::uint64_t()> received;
        std::function<bool()> up;
        std::function<void(std::uint64_t)> expect;
    };

    static LinkRegistry& get();

    void add(Entry entry);
    void remove(Kind kind, const void* owner);

    //! Links started in this process, and how many of them have their peer
    std::uint32_t started() const;
    std::uint32_t connected() const;
    //! What each link sent and received, as `link.<name>.sent` and `link.<name>.received`
    void traffic(JsonObj& out) const;
    //! Tell the link of this name how many messages will have arrived before the next tick.
    //! False when no link of that name is started.
    bool expect(const std::string& name, std::uint64_t received) const;
    //! The first link of this kind and name started in this process, or null
    const void* find(Kind kind, const std::string& name) const;
};

//! One end of a byte stream link to another simulated deployment.
//! Parameters: endpoint, link (name), role ("listen" or "connect"), address (socket path;
//! default <WSTS_LINK_DIR>/<link>.sock).
class LinkByteStream : public Model, public ByteStreamDevice, public Inbound {
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

    //! Messages sent to the peer (or waiting for it, in lockstep), and received from it
    std::uint64_t sent() const { return m_sent.load(); }
    std::uint64_t received() const;
    //! Lockstep: this many messages will have arrived before the next tick takes what is due
    void expect(std::uint64_t received);
    std::string deliverDue(std::uint64_t beforeNs, std::chrono::steady_clock::time_point limit) override;

    //! Every link started in this process, and how many of them are up (bridge status):
    //! both kinds, through the shared registry
    static std::uint32_t started();
    static std::uint32_t connected();
    //! The byte stream link of this name started in this process, or null
    static LinkByteStream* find(const std::string& name);
    //! What each link started in this process sent and received, as `link.<name>.sent` and
    //! `link.<name>.received` (bridge status): both kinds, through the shared registry
    static void traffic(JsonObj& out);

  private:
    void run();
    int listenSocket();
    int connectSocket();
    //! Receive from the peer until it disconnects or the link is stopped
    void serve(int fd);
    bool readFully(int fd, std::uint8_t* data, std::size_t size);
    //! Write one message (m_sendMutex held). False if the peer is not there.
    bool writeMessage(std::uint64_t timeNs, const Bytes& data);

    struct Message {
        std::uint64_t timeNs;  //!< simulated time it was sent at
        Bytes data;
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
    std::deque<Message> m_inbox;   //!< lockstep: arrived, not yet due
    std::uint64_t m_received = 0;
    std::uint64_t m_expected = 0;
};

}  // namespace Wsts

#endif
