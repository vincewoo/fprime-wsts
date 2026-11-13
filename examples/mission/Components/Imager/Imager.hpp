#ifndef COMPONENTS_IMAGER_HPP
#define COMPONENTS_IMAGER_HPP

#include "Components/Imager/Camera.hpp"
#include "Components/Imager/ImagerComponentAc.hpp"

namespace Components {

class Imager final : public ImagerComponentBase {
  public:
    explicit Imager(const char* compName);
    ~Imager() = default;

    //! Open the camera and set its exposure. Called from the topology set-up.
    void configure(U32 exposureTicks);

  private:
    void CAPTURE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) override;

    U8 m_frame[Camera::FRAME_SIZE];
    U32 m_exposure;
    U32 m_frames;
    I32 m_status;  //!< What opening the camera returned
};

}  // namespace Components

#endif
