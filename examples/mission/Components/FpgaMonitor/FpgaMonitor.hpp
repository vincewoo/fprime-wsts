#ifndef COMPONENTS_FPGA_MONITOR_HPP
#define COMPONENTS_FPGA_MONITOR_HPP

#include "Components/FpgaMonitor/FpgaMonitorComponentAc.hpp"

namespace Components {

class FpgaMonitor final : public FpgaMonitorComponentBase {
  public:
    // Register offsets of the housekeeping block
    static constexpr U64 REG_ID = 0x00;           //!< Identity, read only
    static constexpr U64 REG_STATUS = 0x04;       //!< Interrupt status, write 1 to clear
    static constexpr U64 REG_FRAME_COUNT = 0x08;  //!< Frames counted
    static constexpr U64 REG_TEMPERATURE = 0x0C;  //!< Tenths of a degree Celsius
    static constexpr U64 REG_HEARTBEAT = 0x10;    //!< Written every sample: the block watches it
    static constexpr U64 REG_LEDS = 0x14;

    static constexpr U32 BLOCK_ID = 0xF00D0001;
    static constexpr U32 STATUS_FRAME_READY = 0x1;
    static constexpr U32 STATUS_OVERRUN = 0x2;
    static constexpr U32 READ_INVALID = 0xFFFFFFFF;  //!< What a read gives when nothing answers

    explicit FpgaMonitor(const char* compName);
    ~FpgaMonitor() = default;

  private:
    void interruptIn_handler(FwIndexType portNum, U32 number) override;
    void run_handler(FwIndexType portNum, U32 context) override;
    void serviceInterrupt_internalInterfaceHandler(U32 number) override;
    void FPGA_SET_LEDS_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 pattern) override;

    bool m_identified;
    U32 m_heartbeat;
    U32 m_interrupts;
};

}  // namespace Components

#endif
