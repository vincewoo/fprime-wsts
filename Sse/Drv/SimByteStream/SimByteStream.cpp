// ======================================================================
// \title  SimByteStream.cpp
// \brief  Simulation surrogate for byte stream drivers
// ======================================================================
#include <Sse/Drv/SimByteStream/SimByteStream.hpp>

#include <cstdlib>
#include <cstring>

#include <Sse/Core/Bus.hpp>

namespace Sse {

namespace {
//! Marks buffers this component allocated itself (allocate port not connected)
const U32 OWN_BUFFER_CONTEXT = 0x53534542;
}  // namespace

SimByteStream::SimByteStream(const char* compName)
    : SimByteStreamComponentBase(compName), m_chunk(1024), m_ready(false) {}

SimByteStream::~SimByteStream() {
    if (!m_endpoint.empty()) {
        Bus::get().unbind(m_endpoint);
    }
}

void SimByteStream::sseBind(const char* endpoint, const char* options) {
    m_endpoint = endpoint;
    const char* chunk = std::strstr(options, "chunk=");
    if (chunk != nullptr) {
        const long value = std::strtol(chunk + 6, nullptr, 0);
        if (value > 0) {
            m_chunk = static_cast<FwSizeType>(value);
        }
    }
    Bus::get().bindByteStream(m_endpoint, [this](const Bytes& data) { this->receive(data); });
}

void SimByteStream::signalReady() {
    if (!m_ready.exchange(true) && this->isConnected_ready_OutputPort(0)) {
        this->ready_out(0);
    }
}

Drv::ByteStreamStatus SimByteStream::send_handler(FwIndexType portNum, Fw::Buffer& sendBuffer) {
    (void)portNum;
    this->signalReady();
    const U8* data = sendBuffer.getData();
    const Bytes bytes(data, data + sendBuffer.getSize());
    const int status = Bus::get().byteStreamSend(m_endpoint, bytes);
    if (status == 0) {
        return Drv::ByteStreamStatus::OP_OK;
    }
    return (status == Drv::ByteStreamStatus::SEND_RETRY) ? Drv::ByteStreamStatus::SEND_RETRY
                                                         : Drv::ByteStreamStatus::OTHER_ERROR;
}

void SimByteStream::recvReturnIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    if (fwBuffer.getContext() == OWN_BUFFER_CONTEXT) {
        delete[] fwBuffer.getData();
    } else if (this->isConnected_deallocate_OutputPort(0)) {
        this->deallocate_out(0, fwBuffer);
    }
}

void SimByteStream::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    // A real driver is ready once opened; make sure clients that wait for the ready
    // signal get it even if the topology never calls open().
    this->signalReady();
}

void SimByteStream::receive(const Bytes& data) {
    if (!this->isConnected_recv_OutputPort(0)) {
        return;
    }
    // Deliveries can come from the tick thread, the bridge or a command: keep them ordered
    Os::ScopeLock lock(m_receiveLock);
    FwSizeType offset = 0;
    while (offset < data.size()) {
        const FwSizeType size = FW_MIN(m_chunk, static_cast<FwSizeType>(data.size()) - offset);
        Fw::Buffer buffer;
        if (this->isConnected_allocate_OutputPort(0)) {
            buffer = this->allocate_out(0, static_cast<Fw::Buffer::SizeType>(size));
            if (buffer.getData() == nullptr || buffer.getSize() < size) {
                // Out of buffers, as a real driver would be: the data is lost
                if (buffer.getData() != nullptr) {
                    this->deallocate_out(0, buffer);
                }
                return;
            }
        } else {
            buffer.set(new U8[size], static_cast<Fw::Buffer::SizeType>(size), OWN_BUFFER_CONTEXT);
        }
        (void)std::memcpy(buffer.getData(), data.data() + offset, size);
        buffer.setSize(static_cast<Fw::Buffer::SizeType>(size));
        this->recv_out(0, buffer, Drv::ByteStreamStatus::OP_OK);
        offset += size;
    }
}

}  // namespace Sse
