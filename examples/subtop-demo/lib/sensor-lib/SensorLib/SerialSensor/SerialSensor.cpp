#include "SensorLib/SerialSensor/SerialSensor.hpp"

#include <cstdlib>
#include <cstring>

namespace SensorLib {

SerialSensor::SerialSensor(const char* compName)
    : SerialSensorComponentBase(compName), m_line{0}, m_length(0), m_lines(0), m_badLines(0) {}

void SerialSensor::drvConnected_handler(FwIndexType portNum) {
    (void)portNum;
    this->log_ACTIVITY_HI_SensorLinkReady();
}

void SerialSensor::drvReceiveIn_handler(FwIndexType portNum, Fw::Buffer& buffer, const Drv::ByteStreamStatus& status) {
    (void)portNum;
    if (status == Drv::ByteStreamStatus::OP_OK) {
        const U8* data = buffer.getData();
        for (FwSizeType i = 0; i < buffer.getSize(); i++) {
            const char c = static_cast<char>(data[i]);
            if (c == '\n') {
                this->handleLine();
                m_length = 0;
            } else if (c != '\r' && m_length < sizeof(m_line) - 1) {
                m_line[m_length++] = c;
            }
        }
    }
    this->drvReceiveReturnOut_out(0, buffer);
}

void SerialSensor::handleLine() {
    m_line[m_length] = '\0';
    static const char PREFIX[] = "$TEMP,";
    char* end = nullptr;
    const bool hasPrefix = (std::strncmp(m_line, PREFIX, sizeof(PREFIX) - 1) == 0);
    const F32 value = hasPrefix ? std::strtof(m_line + sizeof(PREFIX) - 1, &end) : 0.0f;
    if (hasPrefix && end != m_line + sizeof(PREFIX) - 1 && *end == '\0') {
        m_lines++;
        this->tlmWrite_Temperature(value);
        this->tlmWrite_LinesReceived(m_lines);
    } else {
        m_badLines++;
        this->tlmWrite_BadLines(m_badLines);
        this->log_WARNING_LO_UnrecognizedLine(Fw::String(m_line));
    }
}

void SerialSensor::SEND_QUERY_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& query) {
    char text[48];
    const FwSizeType length = FW_MIN(static_cast<FwSizeType>(query.length()), sizeof(text) - 1);
    (void)std::memcpy(text, query.toChar(), length);
    text[length] = '\n';
    Fw::Buffer buffer(reinterpret_cast<U8*>(text), static_cast<Fw::Buffer::SizeType>(length + 1));
    const Drv::ByteStreamStatus status = this->drvSendOut_out(0, buffer);
    if (status != Drv::ByteStreamStatus::OP_OK) {
        this->log_WARNING_HI_QueryFailed(status);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return;
    }
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace SensorLib
