#include "SensorSim/SerialSensorSim/SerialSensorSim.hpp"

#include <cstdio>
#include <cstring>

namespace SensorSim {

SerialSensorSim::SerialSensorSim(const char* compName)
    : SerialSensorSimComponentBase(compName), m_prefix("$TEMP"), m_connected(false), m_bias(0.0f), m_reports(0), m_queries(0) {}

void SerialSensorSim::configure(const char* reportPrefix) {
    m_prefix = reportPrefix;
}

void SerialSensorSim::drvConnected_handler(FwIndexType portNum) {
    (void)portNum;
    m_connected = true;
}

void SerialSensorSim::drvReceiveIn_handler(FwIndexType portNum, Fw::Buffer& buffer, const Drv::ByteStreamStatus& status) {
    (void)portNum;
    if (status == Drv::ByteStreamStatus::OP_OK) {
        char query[41];
        const FwSizeType length = FW_MIN(static_cast<FwSizeType>(buffer.getSize()), sizeof(query) - 1);
        (void)std::memcpy(query, buffer.getData(), length);
        query[length] = '\0';
        query[std::strcspn(query, "\r\n")] = '\0';
        m_queries++;
        this->tlmWrite_QueriesReceived(m_queries);
        this->log_ACTIVITY_LO_QueryReceived(Fw::String(query));
    }
    this->drvReceiveReturnOut_out(0, buffer);
}

void SerialSensorSim::schedIn_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    // A queued component handles its queue on the thread that schedules it
    while (this->doDispatch() == Fw::QueuedComponentBase::MSG_DISPATCH_OK) {
    }
    if (m_connected) {
        this->sendReport();
    }
}

void SerialSensorSim::sendReport() {
    Fw::ParamValid valid = Fw::ParamValid::INVALID;
    const F32 temperature = this->paramGet_TEMPERATURE(valid) + m_bias;
    char line[32];
    const int length = std::snprintf(line, sizeof(line), "%s,%.2f\n", m_prefix, static_cast<double>(temperature));
    if (length <= 0 || static_cast<FwSizeType>(length) >= sizeof(line)) {
        return;
    }
    Fw::Buffer buffer = this->bufferGetOut_out(0, static_cast<Fw::Buffer::SizeType>(length));
    if (buffer.getData() == nullptr || buffer.getSize() < static_cast<Fw::Buffer::SizeType>(length)) {
        this->log_WARNING_LO_ReportDropped();
        if (buffer.getData() != nullptr) {
            this->bufferReturnOut_out(0, buffer);
        }
        return;
    }
    (void)std::memcpy(buffer.getData(), line, static_cast<size_t>(length));
    buffer.setSize(static_cast<Fw::Buffer::SizeType>(length));
    // A synchronous send: the buffer stays this component's, and goes back to its manager
    if (this->drvSendOut_out(0, buffer) == Drv::ByteStreamStatus::OP_OK) {
        m_reports++;
        this->tlmWrite_ReportsSent(m_reports);
    }
    this->bufferReturnOut_out(0, buffer);
}

void SerialSensorSim::SET_BIAS_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, F32 bias) {
    m_bias = bias;
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace SensorSim
