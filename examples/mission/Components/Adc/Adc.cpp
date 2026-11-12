#include "Components/Adc/Adc.hpp"

namespace Components {

Adc::Adc(const char* compName) : AdcComponentBase(compName) {}

void Adc::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    // Start bit, single-ended conversion of the channel, then clock out the result
    U8 command[3] = {0x01, static_cast<U8>(0x80 | (DETECTOR_CHANNEL << 4)), 0x00};
    U8 response[3] = {0, 0, 0};
    Fw::Buffer writeBuffer(command, sizeof command);
    Fw::Buffer readBuffer(response, sizeof response);
    const Drv::SpiStatus status = this->spiWriteRead_out(0, writeBuffer, readBuffer);
    if (status != Drv::SpiStatus::SPI_OK) {
        this->log_WARNING_LO_SampleError(status);
        return;
    }
    this->tlmWrite_DetectorCounts(static_cast<U16>(((response[1] & 0x03) << 8) | response[2]));
}

}  // namespace Components
