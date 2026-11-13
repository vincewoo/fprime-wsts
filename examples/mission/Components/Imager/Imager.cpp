#include "Components/Imager/Imager.hpp"

// From the board support package in the flight toolchain's sysroot
#include <board/revision.h>

namespace Components {

Imager::Imager(const char* compName) : ImagerComponentBase(compName), m_frame{}, m_exposure(0), m_frames(0), m_status(0) {}

void Imager::configure(U32 exposureTicks) {
    m_exposure = exposureTicks;
    m_status = Camera::open();
    if (m_status == 0) {
        m_status = Camera::setExposure(exposureTicks);
    }
}

void Imager::CAPTURE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) {
    U32 size = 0;
    const I32 status = (m_status != 0) ? m_status : Camera::capture(m_frame, sizeof m_frame, size);
    if (status != 0 || size == 0) {
        this->log_WARNING_HI_CameraError(status);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return;
    }
    U32 sum = 0;
    for (U32 i = 0; i < size; i++) {
        sum += m_frame[i];
    }
    m_frames++;
    this->tlmWrite_FramesCaptured(m_frames);
    this->log_ACTIVITY_HI_FrameCaptured(size, static_cast<U8>(sum / size), m_exposure, BOARD_REVISION);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

}  // namespace Components
