// ======================================================================
// \title  SunSensor.cpp
// \brief  A sun sensor on SpaceWire, read over RMAP (ECSS-E-ST-50-52C)
// ======================================================================
#include "Components/SunSensor/SunSensor.hpp"

namespace Components {

namespace {

//! The RMAP CRC: CRC-8, polynomial x^8 + x^2 + x + 1, bits reflected, initial value 0
U8 rmapCrc(const U8* data, FwSizeType length) {
    U8 crc = 0;
    for (FwSizeType i = 0; i < length; i++) {
        crc = static_cast<U8>(crc ^ data[i]);
        for (U8 bit = 0; bit < 8; bit++) {
            crc = static_cast<U8>((crc & 1U) ? ((crc >> 1) ^ 0xE0U) : (crc >> 1));
        }
    }
    return crc;
}

U32 bigEndian(const U8* data, FwSizeType count) {
    U32 value = 0;
    for (FwSizeType i = 0; i < count; i++) {
        value = (value << 8) | data[i];
    }
    return value;
}

//! RMAP command codes (write, verify, reply, increment)
constexpr U8 READ = 0x3;          // 0011: read, incrementing
constexpr U8 WRITE_VERIFY = 0xF;  // 1111: verified, acknowledged, incrementing
constexpr U8 PROTOCOL = 0x01;

}  // namespace

SunSensor::SunSensor(const char* compName)
    : SunSensorComponentBase(compName),
      m_packet{},
      m_transaction(0),
      m_readPending(false),
      m_readTransaction(0),
      m_writePending(false),
      m_writeTransaction(0),
      m_writeValue(0),
      m_replies(0),
      m_errors(0),
      m_troubled(false) {}

void SunSensor::send(U8 command, U32 address, U32 length, const U8* data, U32 dataLength) {
    // Target, protocol, instruction (a command, no reply address), key, initiator,
    // transaction, extended address, address, data length, header CRC
    FwSizeType at = 0;
    m_packet[at++] = SENSOR_ADDRESS;
    m_packet[at++] = PROTOCOL;
    m_packet[at++] = static_cast<U8>(0x40U | (command << 2));
    m_packet[at++] = SENSOR_KEY;
    m_packet[at++] = PAYLOAD_ADDRESS;
    m_packet[at++] = static_cast<U8>(m_transaction >> 8);
    m_packet[at++] = static_cast<U8>(m_transaction);
    m_packet[at++] = 0x00;
    for (I32 shift = 24; shift >= 0; shift -= 8) {
        m_packet[at++] = static_cast<U8>(address >> shift);
    }
    for (I32 shift = 16; shift >= 0; shift -= 8) {
        m_packet[at++] = static_cast<U8>(length >> shift);
    }
    m_packet[at] = rmapCrc(m_packet, at);
    at++;
    if (data != nullptr) {
        for (U32 i = 0; i < dataLength; i++) {
            m_packet[at++] = data[i];
        }
        m_packet[at] = rmapCrc(data, dataLength);
        at++;
    }
    Fw::Buffer buffer(m_packet, static_cast<Fw::Buffer::SizeType>(at));
    this->toBufferDriver_out(0, buffer);
}

void SunSensor::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    if (m_readPending) {
        m_errors++;
        m_troubled = true;
        this->log_WARNING_LO_NoReply(m_readTransaction);
    }
    // Pending before it is sent: the reply may come back before the send returns
    m_readTransaction = ++m_transaction;
    m_readPending = true;
    this->send(READ, ADDR_ANGLE, 4, nullptr, 0);
    this->tlmWrite_Replies(m_replies);
    this->tlmWrite_Errors(m_errors);
}

void SunSensor::toBufferDriverReturn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    (void)fwBuffer;  // m_packet: nothing to free
}

void SunSensor::fromBufferDriver_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    this->handle(fwBuffer.getData(), fwBuffer.getSize());
    this->fromBufferDriverReturn_out(0, fwBuffer);
}

void SunSensor::handle(const U8* reply, FwSizeType size) {
    // Initiator, protocol, instruction (a reply), status, target, transaction
    if (reply == nullptr || size < 8 || reply[0] != PAYLOAD_ADDRESS || reply[1] != PROTOCOL ||
        (reply[2] & 0xC0U) != 0 || reply[4] != SENSOR_ADDRESS) {
        return;  // not for this component
    }
    const U16 transaction = static_cast<U16>(bigEndian(reply + 5, 2));
    const U8 status = reply[3];
    if ((reply[2] & 0x20U) != 0) {
        // A write's acknowledgement
        if (!m_writePending || transaction != m_writeTransaction) {
            return;
        }
        m_writePending = false;
        if (size != 8 || rmapCrc(reply, 7) != reply[7]) {
            this->log_WARNING_LO_BadReply(transaction);
        } else if (status != 0) {
            this->log_WARNING_LO_RmapError(transaction, status);
        } else {
            this->log_ACTIVITY_HI_IntegrationSet(m_writeValue);
        }
        return;
    }
    // A read's reply: reserved, data length, header CRC, data, data CRC
    if (!m_readPending || transaction != m_readTransaction) {
        return;
    }
    m_readPending = false;
    const FwSizeType length = (size >= 12) ? bigEndian(reply + 8, 3) : 0;
    if (size < 12 || rmapCrc(reply, 11) != reply[11] || size != 12 + length + 1 ||
        rmapCrc(reply + 12, length) != reply[12 + length]) {
        m_errors++;
        m_troubled = true;
        this->log_WARNING_LO_BadReply(transaction);
        return;
    }
    if (status != 0 || length != 4) {
        m_errors++;
        m_troubled = true;
        this->log_WARNING_LO_RmapError(transaction, status);
        return;
    }
    if (m_troubled) {
        // The sensor answers again: the next outage is reported afresh, not swallowed by
        // the throttles the last one used up
        m_troubled = false;
        this->log_WARNING_LO_NoReply_ThrottleClear();
        this->log_WARNING_LO_BadReply_ThrottleClear();
        this->log_WARNING_LO_RmapError_ThrottleClear();
    }
    m_replies++;
    const I32 millidegrees = static_cast<I32>(bigEndian(reply + 12, 4));
    this->tlmWrite_SunAngle(static_cast<F32>(millidegrees) / 1000.0F);
}

void SunSensor::SET_INTEGRATION_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U16 ms) {
    const U8 data[2] = {static_cast<U8>(ms >> 8), static_cast<U8>(ms)};
    m_writeTransaction = ++m_transaction;
    m_writePending = true;
    m_writeValue = ms;
    this->send(WRITE_VERIFY, ADDR_INTEGRATION, sizeof data, data, sizeof data);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Components
