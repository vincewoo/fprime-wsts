// ======================================================================
// \title  BusByteStreamDevice.cpp
// \brief  Adapter from a WSTS byte stream endpoint to an F Prime device simulator
// ======================================================================
#include <Wsts/Drv/BusByteStreamDevice/BusByteStreamDevice.hpp>

#include <cstdlib>
#include <cstring>

#include <Wsts/Core/Control.hpp>

namespace Wsts {

namespace {
//! Marks buffers this component allocated itself (allocate port not connected)
const U32 OWN_BUFFER_CONTEXT = 0x53534244;
}  // namespace

BusByteStreamDevice::BusByteStreamDevice(const char* compName)
    : BusByteStreamDeviceComponentBase(compName), m_chunk(1024), m_ready(false) {}

BusByteStreamDevice::~BusByteStreamDevice() {
    if (!m_endpoint.empty()) {
        Bus::get().detachByteStream(m_endpoint, this);
    }
}

void BusByteStreamDevice::wstsServe(const char* endpoint, const char* options) {
    m_endpoint = endpoint;
    const char* chunk = std::strstr(options, "chunk=");
    if (chunk != nullptr) {
        const long value = std::strtol(chunk + 6, nullptr, 0);
        if (value > 0) {
            m_chunk = static_cast<FwSizeType>(value);
        }
    }
    Bus::get().attachByteStream(m_endpoint, this);
    // The simulator hears "ready" from the first tick on which the flight side is bound,
    // as a client hears it from a driver whose port has been opened
    Control::get().addTickListener([this](std::uint64_t, bool) { this->signalReady(); });
}

void BusByteStreamDevice::signalReady() {
    if (m_ready.load() || m_endpoint.empty() || !Bus::get().byteStreamBound(m_endpoint)) {
        return;
    }
    if (!m_ready.exchange(true) && this->isConnected_ready_OutputPort(0)) {
        this->ready_out(0);
    }
}

void BusByteStreamDevice::onBytes(const Bytes& tx, ByteSink& reply) {
    // The simulator replies through the send port, in its own time
    (void)reply;
    this->signalReady();
    if (!this->isConnected_recv_OutputPort(0)) {
        return;
    }
    // No lock is held here: a simulator whose receive port is synchronous may send its
    // reply, and return this buffer, from inside the call
    FwSizeType offset = 0;
    while (offset < tx.size()) {
        const FwSizeType size = FW_MIN(m_chunk, static_cast<FwSizeType>(tx.size()) - offset);
        Fw::Buffer buffer;
        if (this->isConnected_allocate_OutputPort(0)) {
            buffer = this->allocate_out(0, static_cast<Fw::Buffer::SizeType>(size));
            if (buffer.getData() == nullptr || buffer.getSize() < size) {
                // Out of buffers, as a device with a full receive FIFO: the data is lost
                if (buffer.getData() != nullptr) {
                    this->deallocate_out(0, buffer);
                }
                return;
            }
        } else {
            buffer.set(new U8[size], static_cast<Fw::Buffer::SizeType>(size), OWN_BUFFER_CONTEXT);
        }
        (void)std::memcpy(buffer.getData(), tx.data() + offset, size);
        buffer.setSize(static_cast<Fw::Buffer::SizeType>(size));
        this->recv_out(0, buffer, Drv::ByteStreamStatus::OP_OK);
        offset += size;
    }
}

Drv::ByteStreamStatus BusByteStreamDevice::send_handler(FwIndexType portNum, Fw::Buffer& sendBuffer) {
    (void)portNum;
    if (m_endpoint.empty()) {
        return Drv::ByteStreamStatus::OTHER_ERROR;
    }
    const U8* data = sendBuffer.getData();
    const Bytes bytes(data, data + sendBuffer.getSize());
    // Towards flight software, through the fault table, recorded as the device's output.
    // The caller keeps the buffer, as with any synchronous byte stream driver.
    Bus::get().byteStreamInject(m_endpoint, bytes, "model");
    return Drv::ByteStreamStatus::OP_OK;
}

void BusByteStreamDevice::recvReturnIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    if (fwBuffer.getContext() == OWN_BUFFER_CONTEXT) {
        delete[] fwBuffer.getData();
    } else if (this->isConnected_deallocate_OutputPort(0)) {
        this->deallocate_out(0, fwBuffer);
    }
}

}  // namespace Wsts
