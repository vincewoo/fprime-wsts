#ifndef COMPONENTS_INSTRUMENT_HPP
#define COMPONENTS_INSTRUMENT_HPP

#include "Components/Instrument/InstrumentComponentAc.hpp"

namespace Components {

class Instrument final : public InstrumentComponentBase {
  public:
    explicit Instrument(const char* compName);
    ~Instrument() = default;

  private:
    void run_handler(FwIndexType portNum, U32 context) override;
    void TAKE_SAMPLE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 gain) override;

    U32 m_samples;
    U32 m_uptime;
};

}  // namespace Components

#endif
