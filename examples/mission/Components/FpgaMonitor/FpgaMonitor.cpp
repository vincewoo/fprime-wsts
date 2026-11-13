#include "Components/FpgaMonitor/FpgaMonitor.hpp"

namespace Components {

FpgaMonitor::FpgaMonitor(const char* compName)
    : FpgaMonitorComponentBase(compName), m_identified(false), m_heartbeat(0), m_interrupts(0) {}

void FpgaMonitor::interruptIn_handler(FwIndexType portNum, U32 number) {
    (void)portNum;
    // Interrupt context: no register access here, only a message to this component's queue
    this->serviceInterrupt_internalInterfaceInvoke(number);
}

void FpgaMonitor::serviceInterrupt_internalInterfaceHandler(U32 number) {
    (void)number;
    const U32 status = this->regRead_out(0, REG_STATUS);
    this->regWrite_out(0, REG_STATUS, status);  // write 1 to clear what was seen
    m_interrupts++;
    this->tlmWrite_Interrupts(m_interrupts);
    if ((status & STATUS_FRAME_READY) != 0) {
        this->tlmWrite_FrameCount(this->regRead_out(0, REG_FRAME_COUNT));
    }
    if ((status & STATUS_OVERRUN) != 0) {
        this->log_WARNING_LO_FrameOverrun();
    }
}

void FpgaMonitor::run_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    if (!m_identified) {
        // At start-up, and again after the block stopped answering
        const U32 id = this->regRead_out(0, REG_ID);
        if (id != BLOCK_ID) {
            this->log_WARNING_HI_FpgaIdMismatch(id);
            return;
        }
        m_identified = true;
        this->log_WARNING_HI_FpgaIdMismatch_ThrottleClear();
        this->log_WARNING_LO_FpgaReadInvalid_ThrottleClear();
        this->log_ACTIVITY_HI_FpgaIdentified(id);
    }
    const U32 tenths = this->regRead_out(0, REG_TEMPERATURE);
    if (tenths == READ_INVALID) {
        // Nothing drives the bus: the block is in reset or its clock has stopped
        m_identified = false;
        this->log_WARNING_LO_FpgaReadInvalid(static_cast<U32>(REG_TEMPERATURE));
        return;
    }
    this->tlmWrite_Temperature(static_cast<F32>(static_cast<I32>(tenths)) / 10.0f);
    m_heartbeat++;
    this->regWrite_out(0, REG_HEARTBEAT, m_heartbeat);
    this->tlmWrite_Heartbeat(m_heartbeat);
}

void FpgaMonitor::FPGA_SET_LEDS_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 pattern) {
    this->regWrite_out(0, REG_LEDS, pattern);
    this->log_ACTIVITY_HI_LedsSet(pattern);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Components
