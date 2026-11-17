// ======================================================================
// \title  LinkRegistry.hpp
// \brief  Links started in this process, whatever their transport
//
// A link of a system runs as one end in each of two deployments' processes. The bridge
// reports what each link sent and received, and the conductor of a system in lockstep asks
// a link how many messages will have arrived before the next tick. That bookkeeping does
// not depend on the transport: a stream socket and a datagram socket register alike, so
// that `status`, `advance` and the conductor see every link of an element.
//
// `links_up` is the exception: only a link with a connection can be up. A datagram link
// has none, and reports connected() = false while still counting in `links`.
// ======================================================================
#ifndef WSTS_CORE_LINKREGISTRY_HPP
#define WSTS_CORE_LINKREGISTRY_HPP

#include <cstdint>
#include <string>

#include <Wsts/Core/Json.hpp>

namespace Wsts {

class LinkByteStream;

//! One end of a link between simulated deployments, as the bridge and the conductor see it
class LinkEnd {
  public:
    virtual ~LinkEnd() = default;

    //! The name the link is addressed by (the same at both ends)
    virtual const std::string& name() const = 0;
    //! Messages sent to the peer (or waiting for it, in lockstep), and received from it
    virtual std::uint64_t sent() const = 0;
    virtual std::uint64_t received() const = 0;
    //! Whether the peer is reachable: false for a transport with no connection
    virtual bool isUp() const = 0;
    //! Lockstep: this many messages will have arrived before the next tick takes what is due
    virtual void expect(std::uint64_t received) = 0;

    //! This link as a stream link, or null for another transport
    virtual LinkByteStream* byteStream() { return nullptr; }
};

//! The links started in this process, in the order they were started
class LinkRegistry {
  public:
    LinkRegistry() = delete;

    static void add(LinkEnd* link);
    static void remove(LinkEnd* link);

    //! Links started in this process, and how many of them have a connected peer
    static std::uint32_t started();
    static std::uint32_t connected();

    //! The link of this name started in this process, or null
    static LinkEnd* find(const std::string& name);

    //! What each link started in this process sent and received, as `link.<name>.sent` and
    //! `link.<name>.received` (bridge status)
    static void traffic(JsonObj& out);
};

}  // namespace Wsts

#endif
