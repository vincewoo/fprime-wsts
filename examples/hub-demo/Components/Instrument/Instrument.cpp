#include "Components/Instrument/Instrument.hpp"

namespace Components {

Instrument::Instrument(const char* compName) : InstrumentComponentBase(compName), m_samples(0), m_uptime(0) {}

void Instrument::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    m_uptime++;
    this->tlmWrite_Uptime(m_uptime);
    this->tlmWrite_Samples(m_samples);
}

void Instrument::TAKE_SAMPLE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 gain) {
    m_samples++;
    this->log_ACTIVITY_HI_SampleTaken(m_samples, gain);
    this->tlmWrite_Samples(m_samples);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Components
