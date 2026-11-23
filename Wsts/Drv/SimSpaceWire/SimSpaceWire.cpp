// ======================================================================
// \title  SimSpaceWire.cpp
// \brief  Simulation surrogate for SpaceWire drivers (Drv.PassiveBufferDriver)
// ======================================================================
#include <Wsts/Drv/SimSpaceWire/SimSpaceWire.hpp>

#include <cstring>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

namespace {
//! Marks buffers this component allocated itself (allocate port not connected)
const U32 OWN_BUFFER_CONTEXT = 0x53535057;
}  // namespace

SimSpaceWire::SimSpaceWire(const char* compName) : SimSpaceWireComponentBase(compName) {}

SimSpaceWire::~SimSpaceWire() {
    if (!m_endpoint.empty()) {
        Bus::get().unbind(m_endpoint);
    }
}

void SimSpaceWire::wstsBind(const char* endpoint, const char* options) {
    (void)options;
    m_endpoint = endpoint;
    Bus::get().bindPacket(m_endpoint, [this](const Bytes& packet) { this->receive(packet); });
}

void SimSpaceWire::bufferIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    const U8* data = fwBuffer.getData();
    if (data != nullptr && fwBuffer.getSize() > 0) {
        // One buffer is one packet. A node's reply reaches flight software during the send,
        // before the buffer is returned, as a model's reply does on a byte stream.
        const Bytes packet(data, data + fwBuffer.getSize());
        (void)Bus::get().packetSend(m_endpoint, packet);
    }
    // The buffer driver interface has no status: the buffer goes back whatever happened, as
    // a packet goes onto the link whether anything receives it or not
    if (this->isConnected_bufferInReturn_OutputPort(0)) {
        this->bufferInReturn_out(0, fwBuffer);
    }
}

void SimSpaceWire::bufferOutReturn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    if (fwBuffer.getContext() == OWN_BUFFER_CONTEXT) {
        delete[] fwBuffer.getData();
    } else if (this->isConnected_deallocate_OutputPort(0)) {
        this->deallocate_out(0, fwBuffer);
    }
}

void SimSpaceWire::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
}

void SimSpaceWire::receive(const Bytes& packet) {
    if (!this->isConnected_bufferOut_OutputPort(0) || packet.empty()) {
        return;
    }
    // Deliveries can come from a flight thread (a reply), the tick thread (a node's own
    // packets), the bridge or a command: keep them whole and in order
    Os::ScopeLock lock(m_receiveLock);
    const FwSizeType size = static_cast<FwSizeType>(packet.size());
    Fw::Buffer buffer;
    if (this->isConnected_allocate_OutputPort(0)) {
        buffer = this->allocate_out(0, size);
        if (buffer.getData() == nullptr || buffer.getSize() < size) {
            // Out of buffers, as a real driver would be: the packet is lost
            if (buffer.getData() != nullptr) {
                this->deallocate_out(0, buffer);
            }
            return;
        }
    } else {
        buffer.set(new U8[size], static_cast<Fw::Buffer::SizeType>(size), OWN_BUFFER_CONTEXT);
    }
    (void)std::memcpy(buffer.getData(), packet.data(), size);
    buffer.setSize(static_cast<Fw::Buffer::SizeType>(size));
    this->bufferOut_out(0, buffer);
}

}  // namespace Wsts
