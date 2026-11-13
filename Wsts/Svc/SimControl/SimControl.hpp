// ======================================================================
// \title  SimControl.hpp
// \brief  F Prime front end of the WSTS simulation control core
// ======================================================================
#ifndef WSTS_SIM_CONTROL_HPP
#define WSTS_SIM_CONTROL_HPP

#include <atomic>
#include <chrono>

#include <Wsts/Core/Control.hpp>
#include <Wsts/Svc/SimControl/SimControlComponentAc.hpp>

namespace Wsts {

class SimControl final : public SimControlComponentBase {
  public:
    explicit SimControl(const char* compName);
    ~SimControl() = default;

    //! Hook this component to the simulation tick. Called from generated topology code.
    void wstsBind(const char* endpoint, const char* options = "");

  private:
    void tickNotify_internalInterfaceHandler() override;

    void SIM_PAUSE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) override;
    void SIM_RESUME_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) override;
    void SIM_SET_RATE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, F32 rate) override;
    void FAULT_SET_cmdHandler(FwOpcodeType opCode,
                              U32 cmdSeq,
                              const Fw::CmdStringArg& endpoint,
                              const Wsts::SimFaultKind& kind,
                              const Wsts::SimFaultDir& direction,
                              U32 arg,
                              U32 count,
                              U32 ticks) override;
    void FAULT_CLEAR_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& endpoint) override;
    void INJECT_BYTES_cmdHandler(FwOpcodeType opCode,
                                 U32 cmdSeq,
                                 const Fw::CmdStringArg& endpoint,
                                 const Fw::CmdStringArg& data) override;
    void INJECT_TEXT_cmdHandler(FwOpcodeType opCode,
                                U32 cmdSeq,
                                const Fw::CmdStringArg& endpoint,
                                const Fw::CmdStringArg& text) override;
    void GPIO_FORCE_cmdHandler(FwOpcodeType opCode,
                               U32 cmdSeq,
                               const Fw::CmdStringArg& endpoint,
                               const Fw::Logic& level) override;
    void I2C_REG_SET_cmdHandler(FwOpcodeType opCode,
                                U32 cmdSeq,
                                const Fw::CmdStringArg& endpoint,
                                U32 addr,
                                U8 reg,
                                U8 value) override;
    void REG_SET_cmdHandler(FwOpcodeType opCode,
                            U32 cmdSeq,
                            const Fw::CmdStringArg& endpoint,
                            U64 offset,
                            U64 value) override;
    void IRQ_RAISE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& endpoint, U32 number) override;
    void MODEL_SET_cmdHandler(FwOpcodeType opCode,
                              U32 cmdSeq,
                              const Fw::CmdStringArg& model,
                              const Fw::CmdStringArg& key,
                              const Fw::CmdStringArg& value) override;
    void SCENARIO_LOAD_cmdHandler(FwOpcodeType opCode,
                                  U32 cmdSeq,
                                  const Fw::CmdStringArg& path,
                                  const Wsts::SimReplayMode& mode) override;
    void SCENARIO_START_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) override;
    void SCENARIO_STOP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) override;
    void RECORD_START_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& path) override;
    void RECORD_STOP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) override;

    //! Run an action through the control core and answer the command
    void run(FwOpcodeType opCode, U32 cmdSeq, const JsonObj& action);
    //! Turn queued simulation events into F Prime events
    void drainEvents();
    void publishTelemetry();

    std::atomic<bool> m_notifyPending;
    std::chrono::steady_clock::time_point m_lastNotify;
    std::chrono::steady_clock::time_point m_lastTelemetry;
};

}  // namespace Wsts

#endif
