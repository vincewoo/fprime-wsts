// ======================================================================
// \title  SimControl.cpp
// \brief  F Prime front end of the SSE simulation control core
// ======================================================================
#include <Sse/Svc/SimControl/SimControl.hpp>

namespace Sse {

namespace {
const std::chrono::milliseconds NOTIFY_PERIOD(100);
const std::chrono::milliseconds TELEMETRY_PERIOD(1000);
}  // namespace

SimControl::SimControl(const char* compName)
    : SimControlComponentBase(compName),
      m_notifyPending(false),
      m_lastNotify(std::chrono::steady_clock::now()),
      m_lastTelemetry(std::chrono::steady_clock::now() - TELEMETRY_PERIOD) {}

void SimControl::sseBind(const char* endpoint, const char* options) {
    (void)endpoint;
    (void)options;
    // Runs on the tick thread: hand the work to this component's own thread, at a bounded
    // rate so a fast tick cannot flood the queue.
    Control::get().addTickListener([this](std::uint64_t tick, bool paused) {
        (void)tick;
        (void)paused;
        const auto now = std::chrono::steady_clock::now();
        if (now - m_lastNotify < NOTIFY_PERIOD || m_notifyPending.exchange(true)) {
            return;
        }
        m_lastNotify = now;
        this->tickNotify_internalInterfaceInvoke();
    });
}

void SimControl::tickNotify_internalInterfaceHandler() {
    m_notifyPending.store(false);
    this->drainEvents();
    const auto now = std::chrono::steady_clock::now();
    if (now - m_lastTelemetry >= TELEMETRY_PERIOD) {
        m_lastTelemetry = now;
        this->publishTelemetry();
    }
}

void SimControl::publishTelemetry() {
    const SimStatus status = Control::get().status();
    this->tlmWrite_SimTicks(status.tick);
    this->tlmWrite_SimRunState(status.paused ? SimRunState::PAUSED : SimRunState::RUNNING);
    this->tlmWrite_SimTimeScale(static_cast<F32>(status.rate));
    this->tlmWrite_ActiveFaults(status.activeFaults);
    this->tlmWrite_FaultHits(static_cast<U32>(status.counters.faultHits));
    this->tlmWrite_ScenarioRunState(static_cast<SimScenarioState::T>(status.scenarioState));
    this->tlmWrite_ScenarioStep(status.scenarioStep);
    this->tlmWrite_ScenarioSteps(status.scenarioSteps);
    this->tlmWrite_Recording(status.recording);
    this->tlmWrite_BusTransactions(static_cast<U32>(status.counters.transactions));
    this->tlmWrite_BusBytes(static_cast<U32>(status.counters.bytes));
    this->tlmWrite_BusErrors(static_cast<U32>(status.counters.errors));
}

void SimControl::drainEvents() {
    SimEvent event;
    while (Control::get().popEvent(event)) {
        const Fw::String endpoint(event.endpoint.c_str());
        const Fw::String text(event.text.c_str());
        switch (event.code) {
            case SimEvent::PAUSED:
                this->log_ACTIVITY_HI_SimPaused(event.a);
                break;
            case SimEvent::RESUMED:
                this->log_ACTIVITY_HI_SimResumed(event.a);
                break;
            case SimEvent::RATE_SET:
                this->log_ACTIVITY_HI_SimRateSet(text);
                break;
            case SimEvent::FAULT_ARMED:
                this->log_ACTIVITY_HI_FaultArmed(event.a, endpoint, text, event.b);
                break;
            case SimEvent::FAULT_TRIGGERED:
                this->log_WARNING_LO_FaultTriggered(event.a, endpoint, text);
                break;
            case SimEvent::FAULT_EXPIRED:
                this->log_ACTIVITY_HI_FaultExpired(event.a, endpoint, text, event.b);
                break;
            case SimEvent::FAULTS_CLEARED:
                this->log_ACTIVITY_HI_FaultsCleared(endpoint, event.a);
                break;
            case SimEvent::INJECTED:
                this->log_ACTIVITY_LO_Injected(endpoint, text, event.a, event.b);
                break;
            case SimEvent::SCENARIO_LOADED:
                this->log_ACTIVITY_HI_ScenarioLoaded(text, event.a);
                break;
            case SimEvent::SCENARIO_STARTED:
                this->log_ACTIVITY_HI_ScenarioStarted(text, event.a);
                break;
            case SimEvent::SCENARIO_COMPLETED:
                this->log_ACTIVITY_HI_ScenarioCompleted(text, event.a);
                break;
            case SimEvent::SCENARIO_STOPPED:
                this->log_ACTIVITY_HI_ScenarioStopped(text, event.a);
                break;
            case SimEvent::SCENARIO_ERROR:
                this->log_WARNING_HI_ScenarioError(text);
                break;
            case SimEvent::RECORD_STARTED:
                this->log_ACTIVITY_HI_RecordStarted(text);
                break;
            case SimEvent::RECORD_STOPPED:
                this->log_ACTIVITY_HI_RecordStopped(event.a);
                break;
            case SimEvent::ACTION_FAILED:
                this->log_WARNING_HI_ActionFailed(text);
                break;
            case SimEvent::LOG:
                this->log_ACTIVITY_LO_SimLog(text);
                break;
        }
    }
}

void SimControl::run(FwOpcodeType opCode, U32 cmdSeq, const JsonObj& action) {
    std::string error;
    const bool ok = Control::get().exec(action, error);
    // Report the outcome before the command response so the two arrive in order
    this->drainEvents();
    this->publishTelemetry();
    this->cmdResponse_out(opCode, cmdSeq, ok ? Fw::CmdResponse::OK : Fw::CmdResponse::EXECUTION_ERROR);
}

// ----------------------------------------------------------------------
// Command handlers
// ----------------------------------------------------------------------

void SimControl::SIM_PAUSE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "pause"));
}

void SimControl::SIM_RESUME_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "resume"));
}

void SimControl::SIM_SET_RATE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, F32 rate) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "set_rate").setReal("rate", static_cast<double>(rate)));
}

void SimControl::FAULT_SET_cmdHandler(FwOpcodeType opCode,
                                      U32 cmdSeq,
                                      const Fw::CmdStringArg& endpoint,
                                      const Sse::SimFaultKind& kind,
                                      const Sse::SimFaultDir& direction,
                                      U32 arg,
                                      U32 count,
                                      U32 ticks) {
    JsonObj action;
    action.set("op", "fault_set").set("endpoint", endpoint.toChar());
    action.set("kind", faultKindName(static_cast<FaultKind>(kind.e)));
    action.set("dir", faultDirName(static_cast<FaultDir>(direction.e)));
    action.setNum("arg", arg).setNum("count", count).setNum("ticks", ticks);
    this->run(opCode, cmdSeq, action);
}

void SimControl::FAULT_CLEAR_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& endpoint) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "fault_clear").set("endpoint", endpoint.toChar()));
}

void SimControl::INJECT_BYTES_cmdHandler(FwOpcodeType opCode,
                                         U32 cmdSeq,
                                         const Fw::CmdStringArg& endpoint,
                                         const Fw::CmdStringArg& data) {
    JsonObj action;
    action.set("op", "inject_bytes").set("endpoint", endpoint.toChar()).set("data", data.toChar());
    this->run(opCode, cmdSeq, action);
}

void SimControl::INJECT_TEXT_cmdHandler(FwOpcodeType opCode,
                                        U32 cmdSeq,
                                        const Fw::CmdStringArg& endpoint,
                                        const Fw::CmdStringArg& text) {
    JsonObj action;
    action.set("op", "inject_bytes").set("endpoint", endpoint.toChar()).set("text", text.toChar());
    this->run(opCode, cmdSeq, action);
}

void SimControl::GPIO_FORCE_cmdHandler(FwOpcodeType opCode,
                                       U32 cmdSeq,
                                       const Fw::CmdStringArg& endpoint,
                                       const Fw::Logic& level) {
    JsonObj action;
    action.set("op", "gpio_force").set("endpoint", endpoint.toChar()).setBool("level", level == Fw::Logic::HIGH);
    this->run(opCode, cmdSeq, action);
}

void SimControl::I2C_REG_SET_cmdHandler(FwOpcodeType opCode,
                                        U32 cmdSeq,
                                        const Fw::CmdStringArg& endpoint,
                                        U32 addr,
                                        U8 reg,
                                        U8 value) {
    JsonObj action;
    action.set("op", "i2c_reg_set").set("endpoint", endpoint.toChar());
    action.setNum("addr", addr).setNum("reg", reg).setNum("value", value);
    this->run(opCode, cmdSeq, action);
}

void SimControl::MODEL_SET_cmdHandler(FwOpcodeType opCode,
                                      U32 cmdSeq,
                                      const Fw::CmdStringArg& model,
                                      const Fw::CmdStringArg& key,
                                      const Fw::CmdStringArg& value) {
    JsonObj action;
    action.set("op", "model_set").set("model", model.toChar()).set("key", key.toChar());
    this->run(opCode, cmdSeq, action.set("value", value.toChar()));
}

void SimControl::SCENARIO_LOAD_cmdHandler(FwOpcodeType opCode,
                                          U32 cmdSeq,
                                          const Fw::CmdStringArg& path,
                                          const Sse::SimReplayMode& mode) {
    JsonObj action;
    action.set("op", "scenario_load").set("path", path.toChar());
    this->run(opCode, cmdSeq, action.set("mode", (mode == SimReplayMode::RESPONSES) ? "responses" : "stimulus"));
}

void SimControl::SCENARIO_START_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "scenario_start"));
}

void SimControl::SCENARIO_STOP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "scenario_stop"));
}

void SimControl::RECORD_START_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fw::CmdStringArg& path) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "record_start").set("path", path.toChar()));
}

void SimControl::RECORD_STOP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq) {
    JsonObj action;
    this->run(opCode, cmdSeq, action.set("op", "record_stop"));
}

}  // namespace Sse
