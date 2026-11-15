// ======================================================================
// \title  Control.cpp
// \brief  WSTS simulation control core implementation
// ======================================================================
#include <Wsts/Core/Control.hpp>

#include <Wsts/Core/Activity.hpp>
#include <Wsts/Core/Clock.hpp>

#include <algorithm>
#include <chrono>
#include <cstdlib>

namespace Wsts {

namespace {

const std::size_t MAX_QUEUED_EVENTS = 256;

const char* const EVENT_NAMES[] = {
    "paused",           "resumed",           "rate_set",         "fault_armed",      "fault_triggered",
    "fault_expired",    "faults_cleared",    "scenario_loaded",  "scenario_started", "scenario_completed",
    "scenario_stopped", "scenario_error",    "record_started",   "record_stopped",   "injected",
    "action_failed",    "log",
};

const char* const SCENARIO_STATE_NAMES[] = {"idle", "loaded", "running", "completed", "failed"};

//! Scenario and recording paths may be given relative to the directory named by WSTS_DIR.
//! F Prime command strings are short by default (FW_CMD_STRING_MAX_SIZE), so operators
//! name files, not full paths.
std::string resolvePath(const std::string& path) {
    const char* base = std::getenv("WSTS_DIR");
    if (path.empty() || path[0] == '/' || base == nullptr || base[0] == '\0') {
        return path;
    }
    return std::string(base) + "/" + path;
}

}  // namespace

JsonObj SimEvent::toJson() const {
    JsonObj obj;
    obj.set("ev", "sim_event").set("event", EVENT_NAMES[code]).set("endpoint", endpoint).set("text", text);
    obj.setNum("a", a).setNum("b", b);
    return obj;
}

JsonObj SimStatus::toJson() const {
    JsonObj obj;
    obj.setNum("tick", static_cast<std::int64_t>(tick)).setBool("paused", paused).setReal("rate", rate);
    obj.setNum("stepping", static_cast<std::int64_t>(stepping));
    obj.setBool("counting", counting).setNum("activity", static_cast<std::int64_t>(activity));
    obj.setNum("work", static_cast<std::int64_t>(work)).setNum("busy_ticks", static_cast<std::int64_t>(busyTicks));
    obj.setBool("stepped", stepped).setReal("sim_seconds", simSeconds);
    obj.setNum("tick_period_ns", static_cast<std::int64_t>(tickPeriodNs));
    if (!stuck.empty()) {
        obj.set("stuck", stuck);
    }
    obj.setNum("active_faults", activeFaults);
    obj.set("scenario_state", SCENARIO_STATE_NAMES[static_cast<std::uint8_t>(scenarioState)]);
    obj.setNum("scenario_step", scenarioStep).setNum("scenario_steps", scenarioSteps);
    obj.setBool("recording", recording);
    obj.setNum("transactions", static_cast<std::int64_t>(counters.transactions));
    obj.setNum("bytes", static_cast<std::int64_t>(counters.bytes));
    obj.setNum("errors", static_cast<std::int64_t>(counters.errors));
    obj.setNum("fault_hits", static_cast<std::int64_t>(counters.faultHits));
    return obj;
}

Control& Control::get() {
    static Control control;
    return control;
}

Control::Control() {
    Bus::get().setObserver(this);
}

void Control::reset() {
    this->stopInternalTicker();
    this->recordStop();
    {
        std::lock_guard<std::mutex> lock(m_scenarioMutex);
        m_steps.clear();
        m_scenarioState.store(ScenarioState::IDLE);
        m_scenarioNext.store(0);
        m_scenarioSize.store(0);
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_models.clear();
        m_events.clear();
        m_listeners.clear();
        m_recordTap = nullptr;
        m_eventTap = nullptr;
    }
    m_tick.store(0);
    m_paused.store(false);
    m_stepUntil.store(0);
    m_busyTicks.store(0);
    m_stepped.store(false);
    m_steppedRate.store(0.0);
    m_stepLimitMs.store(10000);
    {
        std::lock_guard<std::mutex> lock(m_gateMutex);
        m_tickOpen = false;
        m_actionOpen = false;
        m_stuck.clear();
    }
    Activity::get().reset();
    m_rate.store(1.0);
    m_hasTickSource.store(false);
    {
        std::lock_guard<std::mutex> lock(m_clockMutex);
        m_clocks.clear();
    }
    m_tickPeriodNs.store(0);
    m_measuredPeriodNs.store(0);
    m_lastTickValid = false;
    Bus::get().reset();
    Bus::get().setObserver(this);
}

// ----------------------------------------------------------------------
// Time
// ----------------------------------------------------------------------

void Control::onTick() {
    // Whatever the last tick caused should be finished by now. In real time nothing waits
    // for that: counted here, it says how far a deployment is from being steppable.
    if (Activity::get().pendingElsewhere() > 0) {
        m_busyTicks++;
    }
    // The tick is work itself, from here until its source has cycled the rate groups
    Activity::Scope tickWork;
    const std::uint64_t tick = ++m_tick;
    Bus::get().setTick(tick);

    std::vector<Fault> expired;
    Bus::get().expireFaults(tick, expired);
    for (const Fault& fault : expired) {
        this->post(SimEvent::FAULT_EXPIRED, fault.endpoint, faultKindName(fault.kind), fault.id, fault.hits);
    }

    this->scenarioRun();

    std::vector<Model*> models;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        for (const auto& entry : m_models) {
            models.push_back(entry.second);
        }
    }
    for (Model* model : models) {
        model->onTick(tick);
    }
    this->notifyListeners(false);
    this->grantClocks();

    // The last tick of a `step`: pause here, so that the next tick is not taken. A pause,
    // a resume or another step that arrives meanwhile sets the target itself.
    std::uint64_t until = m_stepUntil.load();
    if (until != 0 && tick >= until && m_stepUntil.compare_exchange_strong(until, 0)) {
        m_paused.store(true);
        this->post(SimEvent::PAUSED, "", "", static_cast<std::uint32_t>(tick));
    }
}

// ----------------------------------------------------------------------
// Stepped time
// ----------------------------------------------------------------------

void Control::setStepped(bool stepped) {
    if (m_stepped.exchange(stepped) == stepped) {
        return;
    }
    m_steppedRate.store(0.0);
    {
        std::lock_guard<std::mutex> lock(m_gateMutex);
        m_stuck.clear();
    }
    m_gate.notify_all();
    this->post(SimEvent::LOG, "", stepped ? "stepped time: a tick is taken when the last one is finished"
                                          : "real time: ticks are paced by the wall clock");
}

std::string Control::stuck() {
    std::lock_guard<std::mutex> lock(m_gateMutex);
    return m_stuck;
}

bool Control::awaitTick(std::uint64_t periodUs, Clock::time_point& next, const std::atomic<bool>& quit) {
    if (!m_stepped.load()) {
        // Real time: simulated time advances `rate` times faster than the wall clock
        const double rate = std::max(0.001, m_rate.load());
        next += std::chrono::microseconds(static_cast<std::int64_t>(static_cast<double>(periodUs) / rate));
        const auto now = Clock::now();
        if (next < now) {
            next = now;  // fell behind (host load, long pause): do not burst to catch up
        }
        std::this_thread::sleep_until(next);
        return !quit.load() && !m_paused.load();
    }
    if (m_paused.load()) {
        // Nothing to wait for but a step or a resume: look again shortly
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
        next = Clock::now();
        return false;
    }
    {
        // Actions from outside go first: a client that pauses a simulation running flat
        // out must not have to win a race against the next tick
        std::unique_lock<std::mutex> lock(m_gateMutex);
        const bool free = m_gate.wait_for(lock, std::chrono::milliseconds(50),
                                          [this]() { return !m_actionOpen && m_actionsWaiting == 0; });
        if (!free || quit.load() || m_paused.load() || !m_stepped.load()) {
            return false;
        }
        m_tickOpen = true;
    }
    const double rate = m_steppedRate.load();
    if (rate > 0.0) {
        // An operator's pace: no faster than this, however quickly ticks finish
        next += std::chrono::microseconds(static_cast<std::int64_t>(static_cast<double>(periodUs) / rate));
        const auto now = Clock::now();
        if (next < now) {
            next = now;
        }
        std::this_thread::sleep_until(next);
    } else {
        next = Clock::now();
    }
    return true;
}

void Control::finishTick() {
    {
        std::lock_guard<std::mutex> lock(m_gateMutex);
        if (!m_tickOpen) {
            return;  // real time, or the mode changed while this tick was being taken
        }
    }
    // The tick is finished when nothing it caused is outstanding. This thread's own scopes
    // are not waited for.
    const auto limit = Clock::now() + std::chrono::milliseconds(m_stepLimitMs.load());
    Activity& activity = Activity::get();
    bool finished = activity.pendingElsewhere() == 0;
    while (!finished && Clock::now() < limit && m_stepped.load()) {
        (void)activity.waitQuiescent(std::chrono::milliseconds(1));
        finished = activity.pendingElsewhere() == 0;
    }
    std::string stuck;
    if (!finished && m_stepped.load()) {
        // Something never comes back for its next message: a handler that blocks, or a
        // task that waits on the host. Stop here rather than run on with work piling up.
        stuck = activity.holders();
        if (stuck.empty()) {
            stuck = std::to_string(activity.pendingElsewhere()) + " piece(s) of work outside the queues";
        }
        {
            // Before the pause shows: whoever sees it paused finds the reason
            std::lock_guard<std::mutex> lock(m_gateMutex);
            m_stuck = stuck;
        }
        m_stepUntil.store(0);
        m_paused.store(true);
        std::fprintf(stderr, "[WSTS] stepped time: tick %llu did not finish within %u ms (work outstanding: %s). "
                             "The simulation is paused.\n",
                     static_cast<unsigned long long>(m_tick.load()), m_stepLimitMs.load(), stuck.c_str());
        this->post(SimEvent::LOG, "", "stepped time: a tick did not finish, paused: " + stuck);
        this->post(SimEvent::PAUSED, "", "", static_cast<std::uint32_t>(m_tick.load()));
    }
    {
        std::lock_guard<std::mutex> lock(m_gateMutex);
        m_tickOpen = false;
    }
    m_gate.notify_all();
}

Control::ActionGate::ActionGate() : m_held(false) {
    Control& control = Control::get();
    if (!control.m_stepped.load()) {
        return;
    }
    std::unique_lock<std::mutex> lock(control.m_gateMutex);
    control.m_actionsWaiting++;
    // Bounded: a tick that cannot finish is reported by its own limit; an action is not
    // held up for ever behind it
    (void)control.m_gate.wait_for(lock, std::chrono::milliseconds(control.m_stepLimitMs.load() + 1000),
                                  [&control]() { return !control.m_tickOpen && !control.m_actionOpen; });
    control.m_actionsWaiting--;
    if (!control.m_tickOpen && !control.m_actionOpen) {
        control.m_actionOpen = true;
        m_held = true;
    }
}

Control::ActionGate::~ActionGate() {
    if (!m_held) {
        return;
    }
    Control& control = Control::get();
    // What the action caused (injected bytes being handled, an interrupt) is part of it
    const auto limit = Clock::now() + std::chrono::milliseconds(control.m_stepLimitMs.load());
    while (Activity::get().pendingElsewhere() > 0 && Clock::now() < limit) {
        (void)Activity::get().waitQuiescent(std::chrono::milliseconds(1));
    }
    {
        std::lock_guard<std::mutex> lock(control.m_gateMutex);
        control.m_actionOpen = false;
    }
    control.m_gate.notify_all();
}

void Control::onIdle() {
    m_lastTickValid = false;  // the interval across a pause says nothing about the period
    this->notifyListeners(true);
}

std::uint64_t Control::tickPeriodNs() const {
    const std::uint64_t declared = m_tickPeriodNs.load();
    return (declared != 0) ? declared : m_measuredPeriodNs.load();
}

void Control::grantClocks() {
    const auto now = std::chrono::steady_clock::now();
    const double rate = std::max(0.001, m_rate.load());
    if (m_tickPeriodNs.load() == 0 && m_lastTickValid) {
        // No tick source declared its period: take it from the ticks themselves. Wall
        // time between two ticks, times the time scale, is the simulated time of one.
        const double measured = static_cast<double>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(now - m_lastTick).count()) * rate;
        const std::uint64_t previous = m_measuredPeriodNs.load();
        m_measuredPeriodNs.store(previous == 0 ? static_cast<std::uint64_t>(measured)
                                               : static_cast<std::uint64_t>(0.9 * static_cast<double>(previous) + 0.1 * measured));
    }
    m_lastTick = now;
    m_lastTickValid = true;
    const std::uint64_t period = this->tickPeriodNs();
    if (period == 0) {
        return;
    }
    std::vector<CycleClock*> clocks;
    {
        std::lock_guard<std::mutex> lock(m_clockMutex);
        clocks = m_clocks;
    }
    for (CycleClock* clock : clocks) {
        clock->grant(period, rate, now);
    }
}

void Control::addClock(CycleClock* clock) {
    std::lock_guard<std::mutex> lock(m_clockMutex);
    if (std::find(m_clocks.begin(), m_clocks.end(), clock) == m_clocks.end()) {
        m_clocks.push_back(clock);
    }
}

void Control::removeClock(CycleClock* clock) {
    std::lock_guard<std::mutex> lock(m_clockMutex);
    m_clocks.erase(std::remove(m_clocks.begin(), m_clocks.end(), clock), m_clocks.end());
}

void Control::stopClocks() {
    std::vector<CycleClock*> clocks;
    {
        std::lock_guard<std::mutex> lock(m_clockMutex);
        clocks = m_clocks;
    }
    for (CycleClock* clock : clocks) {
        clock->stop();
    }
}

void Control::notifyListeners(bool paused) {
    std::vector<TickListener> listeners;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        listeners = m_listeners;
    }
    for (const TickListener& listener : listeners) {
        listener(m_tick.load(), paused);
    }
}

void Control::startInternalTicker(std::uint32_t hz) {
    if (m_ticker.joinable() || hz == 0) {
        return;
    }
    m_tickerQuit.store(false);
    this->setTickPeriod(1000000000ull / hz);
    m_ticker = std::thread([this, hz]() {
        Clock::time_point next = Clock::now();
        while (!m_tickerQuit.load()) {
            if (!this->awaitTick(1000000ull / hz, next, m_tickerQuit)) {
                if (!m_tickerQuit.load()) {
                    this->onIdle();
                }
                continue;
            }
            this->onTick();
            this->finishTick();
        }
    });
}

void Control::stopInternalTicker() {
    m_tickerQuit.store(true);
    if (m_ticker.joinable()) {
        m_ticker.join();
    }
}

// ----------------------------------------------------------------------
// Events, models, observation
// ----------------------------------------------------------------------

void Control::post(SimEvent::Code code,
                   const std::string& endpoint,
                   const std::string& text,
                   std::uint32_t a,
                   std::uint32_t b) {
    SimEvent event;
    event.code = code;
    event.endpoint = endpoint;
    event.text = text;
    event.a = a;
    event.b = b;
    EventTap tap;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_events.size() >= MAX_QUEUED_EVENTS) {
            m_events.pop_front();
        }
        m_events.push_back(event);
        tap = m_eventTap;
    }
    if (tap) {
        tap(event);
    }
}

bool Control::popEvent(SimEvent& event) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_events.empty()) {
        return false;
    }
    event = m_events.front();
    m_events.pop_front();
    return true;
}

void Control::addModel(const std::string& name, Model* model) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_models[name] = model;
}

void Control::addTickListener(TickListener listener) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_listeners.push_back(std::move(listener));
}

void Control::setRecordTap(RecordTap tap) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_recordTap = std::move(tap);
}

void Control::setEventTap(EventTap tap) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_eventTap = std::move(tap);
}

SimStatus Control::status() {
    SimStatus status;
    status.tick = m_tick.load();
    status.paused = m_paused.load();
    const std::uint64_t until = m_stepUntil.load();
    status.stepping = until > status.tick ? until - status.tick : 0;
    status.counting = Activity::get().counting();
    status.activity = Activity::get().pending();
    status.work = Activity::get().total();
    status.busyTicks = m_busyTicks.load();
    status.stepped = m_stepped.load();
    status.tickPeriodNs = this->tickPeriodNs();
    status.simSeconds = static_cast<double>(status.tick) * static_cast<double>(status.tickPeriodNs) / 1e9;
    status.stuck = this->stuck();
    status.rate = m_rate.load();
    if (m_stepped.load()) {
        status.rate = m_steppedRate.load();  // a limit in stepped time; 0: none
    }
    status.activeFaults = Bus::get().activeFaults();
    status.scenarioState = m_scenarioState.load();
    status.scenarioStep = m_scenarioNext.load();
    status.scenarioSteps = m_scenarioSize.load();
    status.recording = m_recording.load();
    status.counters = Bus::get().counters();
    return status;
}

void Control::onRecord(const Record& record) {
    RecordTap tap;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        tap = m_recordTap;
    }
    if (!tap && !m_recording.load()) {
        return;
    }
    const JsonObj line = record.toJson();
    if (tap) {
        tap(line);
    }
    this->recordLine(line);
}

void Control::onFaultHit(const Fault& fault, bool first, bool expired) {
    // Report the first application only: a persistent fault on a polled bus would otherwise
    // produce an event per transaction. The running total is in telemetry.
    if (first) {
        this->post(SimEvent::FAULT_TRIGGERED, fault.endpoint, faultKindName(fault.kind), fault.id, fault.hits);
    }
    if (expired) {
        this->post(SimEvent::FAULT_EXPIRED, fault.endpoint, faultKindName(fault.kind), fault.id, fault.hits);
    }
}

// ----------------------------------------------------------------------
// Actions
// ----------------------------------------------------------------------

bool Control::execLine(const std::string& line, std::string& error) {
    JsonObj action;
    if (!action.parse(line, error)) {
        this->post(SimEvent::ACTION_FAILED, "", error);
        return false;
    }
    return this->exec(action, error);
}

bool Control::exec(const JsonObj& action, std::string& error) {
    const std::string op = action.str("op");
    const bool ok = this->dispatch(action, error);
    if (!ok) {
        this->post(SimEvent::ACTION_FAILED, action.str("endpoint"), op + ": " + error);
        return false;
    }
    // Record what was done so that a recording can be replayed as a scenario. Recorder and
    // scenario management actions are not part of the scenario itself.
    if (op.rfind("record_", 0) != 0 && op.rfind("scenario_", 0) != 0) {
        JsonObj line = action;
        line.setNum("tick", static_cast<std::int64_t>(m_tick.load()));
        RecordTap tap;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            tap = m_recordTap;
        }
        if (tap) {
            tap(line);
        }
        this->recordLine(line);
    }
    return true;
}

bool Control::dispatch(const JsonObj& action, std::string& error) {
    const std::string op = action.str("op");
    Bus& bus = Bus::get();

    if (op == "step") {
        const std::int64_t ticks = action.num("ticks", 1);
        if (ticks < 1 || ticks > 1000000000) {
            error = "ticks must be between 1 and 1000000000";
            return false;
        }
        // Run until the tick count is this much higher, then pause. Counted from the tick
        // the simulation is at: exact from a paused simulation and from a scenario step.
        m_stepUntil.store(m_tick.load() + static_cast<std::uint64_t>(ticks));
        m_paused.store(false);
        this->post(SimEvent::RESUMED, "", "", static_cast<std::uint32_t>(m_tick.load()));
        return true;
    }
    if (op == "pause") {
        m_stepUntil.store(0);
        m_paused.store(true);
        this->post(SimEvent::PAUSED, "", "", static_cast<std::uint32_t>(m_tick.load()));
        return true;
    }
    if (op == "resume") {
        {
            std::lock_guard<std::mutex> lock(m_gateMutex);
            m_stuck.clear();
        }
        m_stepUntil.store(0);
        m_paused.store(false);
        this->post(SimEvent::RESUMED, "", "", static_cast<std::uint32_t>(m_tick.load()));
        return true;
    }
    if (op == "set_stepped") {
        this->setStepped(action.flag("stepped", true));
        return true;
    }
    if (op == "set_rate") {
        const double rate = action.real("rate", 0.0);
        if (m_stepped.load()) {
            // In stepped time the rate is a limit for someone who is watching: 0 takes it away
            if (rate != 0.0 && (rate < 0.01 || rate > 1000.0)) {
                error = "rate must be 0 (as fast as it goes) or between 0.01 and 1000";
                return false;
            }
            m_steppedRate.store(rate);
            this->post(SimEvent::RATE_SET, "", action.str("rate"));
            return true;
        }
        if (rate < 0.01 || rate > 1000.0) {
            error = "rate must be between 0.01 and 1000";
            return false;
        }
        m_rate.store(rate);
        this->post(SimEvent::RATE_SET, "", action.str("rate"));
        return true;
    }
    if (op == "fault_set") {
        return this->faultSet(action, error);
    }
    if (op == "fault_clear") {
        const std::string endpoint = action.str("endpoint", "*");
        const std::uint32_t removed = bus.clearFaults(endpoint);
        this->post(SimEvent::FAULTS_CLEARED, endpoint, "", removed);
        return true;
    }
    if (op == "inject_bytes") {
        return this->injectBytes(action, error);
    }
    if (op == "gpio_force") {
        const std::string endpoint = action.str("endpoint");
        if (!bus.known(endpoint)) {
            error = "unknown endpoint '" + endpoint + "'";
            return false;
        }
        const bool level = action.flag("level");
        bus.gpioSetInput(endpoint, level, "inject");
        this->post(SimEvent::INJECTED, endpoint, level ? "gpio high" : "gpio low", 1);
        return true;
    }
    if (op == "i2c_reg_set") {
        const std::string endpoint = action.str("endpoint");
        const auto addr = static_cast<std::uint32_t>(action.num("addr"));
        const auto reg = static_cast<std::uint8_t>(action.num("reg"));
        const auto value = static_cast<std::uint8_t>(action.num("value"));
        if (!bus.i2cPoke(endpoint, addr, reg, value)) {
            error = "no register-addressable I2C device at '" + endpoint + "' address " + std::to_string(addr);
            return false;
        }
        this->post(SimEvent::INJECTED, endpoint, "i2c register", reg, value);
        return true;
    }
    if (op == "reg_set") {
        const std::string endpoint = action.str("endpoint");
        if (!action.has("offset") || !action.has("value")) {
            error = "reg_set needs 'offset' and 'value'";
            return false;
        }
        const std::uint64_t offset = action.unum("offset");
        const std::uint64_t value = action.unum("value");
        if (!bus.registerPoke(endpoint, offset, value)) {
            error = "no register block at '" + endpoint + "' that accepts a value at offset " + action.str("offset");
            return false;
        }
        this->post(SimEvent::INJECTED, endpoint, "register", static_cast<std::uint32_t>(offset),
                   static_cast<std::uint32_t>(value));
        return true;
    }
    if (op == "irq_raise") {
        const std::string endpoint = action.str("endpoint");
        if (!bus.known(endpoint)) {
            error = "unknown endpoint '" + endpoint + "'";
            return false;
        }
        const auto number = static_cast<std::uint32_t>(action.unum("number"));
        bus.registerInterrupt(endpoint, number, "inject");
        this->post(SimEvent::INJECTED, endpoint, "interrupt", number);
        return true;
    }
    if (op == "model_set") {
        const std::string name = action.str("model");
        Model* model = nullptr;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            const auto it = m_models.find(name);
            model = (it == m_models.end()) ? nullptr : it->second;
        }
        if (model == nullptr) {
            error = "unknown model '" + name + "'";
            return false;
        }
        if (!model->set(action.str("key"), action.str("value"))) {
            error = "model '" + name + "' rejected " + action.str("key") + "=" + action.str("value");
            return false;
        }
        return true;
    }
    if (op == "record_start") {
        return this->recordStart(resolvePath(action.str("path")), error);
    }
    if (op == "record_stop") {
        this->recordStop();
        return true;
    }
    if (op == "scenario_load") {
        ReplayMode mode = ReplayMode::STIMULUS;
        const std::string modeName = action.str("mode", "stimulus");
        if (modeName == "responses" || modeName == "RESPONSES" || modeName == "1") {
            mode = ReplayMode::RESPONSES;
        } else if (modeName != "stimulus" && modeName != "STIMULUS" && modeName != "0") {
            error = "unknown replay mode '" + modeName + "'";
            return false;
        }
        return this->scenarioLoad(resolvePath(action.str("path")), mode, error);
    }
    if (op == "scenario_start") {
        return this->scenarioStart(error);
    }
    if (op == "scenario_stop") {
        this->scenarioStop(false);
        return true;
    }
    if (op == "log") {
        this->post(SimEvent::LOG, "", action.str("text"));
        return true;
    }
    error = op.empty() ? "missing 'op'" : "unknown op '" + op + "'";
    return false;
}

bool Control::faultSet(const JsonObj& action, std::string& error) {
    Fault fault;
    fault.endpoint = action.str("endpoint");
    // The interrupts of a register endpoint are a fault target of their own: "<endpoint>:irq"
    const std::string suffix = Bus::interruptTarget("");
    const bool interrupts = fault.endpoint.size() > suffix.size() &&
                            fault.endpoint.compare(fault.endpoint.size() - suffix.size(), suffix.size(), suffix) == 0;
    const std::string endpoint = interrupts ? fault.endpoint.substr(0, fault.endpoint.size() - suffix.size()) : fault.endpoint;
    if (!Bus::get().known(endpoint)) {
        error = "unknown endpoint '" + fault.endpoint + "'";
        return false;
    }
    if (!faultKindFromName(action.str("kind"), fault.kind)) {
        error = "unknown fault kind '" + action.str("kind") + "'";
        return false;
    }
    if (!faultDirFromName(action.str("dir", "both"), fault.dir)) {
        error = "unknown fault direction '" + action.str("dir") + "'";
        return false;
    }
    fault.arg = static_cast<std::uint32_t>(action.num("arg", 0));
    fault.count = static_cast<std::uint32_t>(action.num("count", 0));
    const std::int64_t ticks = action.num("ticks", 0);
    fault.expireTick = (ticks > 0) ? (m_tick.load() + static_cast<std::uint64_t>(ticks)) : 0;
    const std::uint32_t id = Bus::get().addFault(fault);
    this->post(SimEvent::FAULT_ARMED, fault.endpoint, faultKindName(fault.kind), id, fault.arg);
    return true;
}

bool Control::injectBytes(const JsonObj& action, std::string& error) {
    const std::string endpoint = action.str("endpoint");
    if (!Bus::get().known(endpoint)) {
        error = "unknown endpoint '" + endpoint + "'";
        return false;
    }
    Bytes data;
    if (action.has("text")) {
        const std::string text = action.str("text");
        data.assign(text.begin(), text.end());
    } else if (!fromHex(action.str("data"), data)) {
        error = "'data' is not valid hex";
        return false;
    }
    if (data.empty()) {
        error = "nothing to inject";
        return false;
    }
    Bus::get().byteStreamInject(endpoint, data, "inject");
    this->post(SimEvent::INJECTED, endpoint, "bytes", static_cast<std::uint32_t>(data.size()));
    return true;
}

// ----------------------------------------------------------------------
// Recorder
// ----------------------------------------------------------------------

bool Control::recordStart(const std::string& path, std::string& error) {
    std::lock_guard<std::mutex> lock(m_recordMutex);
    if (m_recording.load()) {
        error = "already recording";
        return false;
    }
    m_recordFile.open(path, std::ios::out | std::ios::trunc);
    if (!m_recordFile.is_open()) {
        error = "cannot open '" + path + "' for writing";
        return false;
    }
    m_recordStartTick = m_tick.load();
    m_recordCount = 0;
    m_recording.store(true);
    this->post(SimEvent::RECORD_STARTED, "", path);
    return true;
}

void Control::recordStop() {
    std::lock_guard<std::mutex> lock(m_recordMutex);
    if (!m_recording.load()) {
        return;
    }
    m_recording.store(false);
    m_recordFile.close();
    this->post(SimEvent::RECORD_STOPPED, "", "", m_recordCount);
}

void Control::recordLine(JsonObj line) {
    if (!m_recording.load()) {
        return;
    }
    std::lock_guard<std::mutex> lock(m_recordMutex);
    if (!m_recording.load()) {
        return;
    }
    // Ticks in a recording are relative to its start, so it can be replayed at any time
    const std::int64_t absolute = line.num("tick", static_cast<std::int64_t>(m_recordStartTick));
    line.setNum("tick", absolute - static_cast<std::int64_t>(m_recordStartTick));
    m_recordFile << line.dump() << "\n";
    m_recordFile.flush();
    m_recordCount++;
}

// ----------------------------------------------------------------------
// Scenario player
// ----------------------------------------------------------------------

bool Control::scenarioLoad(const std::string& path, ReplayMode mode, std::string& error) {
    std::ifstream file(path);
    if (!file.is_open()) {
        error = "cannot open scenario '" + path + "'";
        this->post(SimEvent::SCENARIO_ERROR, "", error);
        return false;
    }
    std::vector<Step> steps;
    std::string line;
    std::uint32_t lineNumber = 0;
    while (std::getline(file, line)) {
        lineNumber++;
        const std::size_t first = line.find_first_not_of(" \t\r");
        if (first == std::string::npos || line[first] == '#') {
            continue;
        }
        JsonObj action;
        std::string parseError;
        if (!action.parse(line, parseError)) {
            error = path + ":" + std::to_string(lineNumber) + ": " + parseError;
            this->post(SimEvent::SCENARIO_ERROR, "", error);
            return false;
        }
        const bool isOp = action.has("op");
        const bool isRec = action.has("rec");
        if (!isOp && !isRec) {
            error = path + ":" + std::to_string(lineNumber) + ": line has neither 'op' nor 'rec'";
            this->post(SimEvent::SCENARIO_ERROR, "", error);
            return false;
        }
        // Recorded transactions only matter when replaying device responses. Injections are
        // already present as actions, so their transaction records are skipped.
        if (isRec && (mode != ReplayMode::RESPONSES || action.str("src") == "inject")) {
            continue;
        }
        steps.push_back(Step{static_cast<std::uint64_t>(std::max<std::int64_t>(0, action.num("tick", 0))), action});
    }
    std::stable_sort(steps.begin(), steps.end(), [](const Step& a, const Step& b) { return a.tick < b.tick; });

    std::lock_guard<std::mutex> lock(m_scenarioMutex);
    if (m_scenarioState.load() == ScenarioState::RUNNING) {
        error = "a scenario is running";
        return false;
    }
    m_steps = std::move(steps);
    m_scenarioMode = mode;
    m_scenarioPath = path;
    m_scenarioNext.store(0);
    m_scenarioSize.store(static_cast<std::uint32_t>(m_steps.size()));
    m_scenarioState.store(ScenarioState::LOADED);
    this->post(SimEvent::SCENARIO_LOADED, "", path, static_cast<std::uint32_t>(m_steps.size()));
    return true;
}

bool Control::scenarioStart(std::string& error) {
    std::lock_guard<std::mutex> lock(m_scenarioMutex);
    const ScenarioState state = m_scenarioState.load();
    if (state == ScenarioState::IDLE) {
        error = "no scenario loaded";
        return false;
    }
    if (state == ScenarioState::RUNNING) {
        error = "scenario already running";
        return false;
    }
    Bus& bus = Bus::get();
    if (m_scenarioMode == ReplayMode::RESPONSES) {
        // Recorded I2C/SPI responses are served in order, per endpoint and I2C address, from
        // the first poll on; recorded register reads per endpoint and offset
        bus.setReplay(true);
        for (const Step& step : m_steps) {
            const std::string rec = step.action.str("rec");
            if (rec == "i2c" || rec == "spi") {
                Bytes read;
                (void)fromHex(step.action.str("read"), read);
                const auto addr = rec == "i2c" ? static_cast<std::uint32_t>(step.action.num("addr")) : 0U;
                bus.queueReplayResponse(step.action.str("endpoint"), addr, static_cast<int>(step.action.num("status")),
                                        read);
            } else if (rec == "reg_rd") {
                Bytes read;
                (void)fromHex(step.action.str("read"), read);
                std::uint64_t value = 0;
                for (std::size_t i = 0; i < read.size() && i < 8; i++) {
                    value |= static_cast<std::uint64_t>(read[i]) << (8 * i);
                }
                bus.queueRegisterReplay(step.action.str("endpoint"), step.action.unum("offset"),
                                        static_cast<int>(step.action.num("status")), value);
            }
        }
    }
    m_scenarioStartTick = m_tick.load();
    m_scenarioNext.store(0);
    m_scenarioState.store(ScenarioState::RUNNING);
    this->post(SimEvent::SCENARIO_STARTED, "", m_scenarioPath, static_cast<std::uint32_t>(m_steps.size()));
    return true;
}

void Control::scenarioStop(bool completed) {
    std::lock_guard<std::mutex> lock(m_scenarioMutex);
    if (m_scenarioState.load() != ScenarioState::RUNNING) {
        return;
    }
    Bus::get().setReplay(false);
    m_scenarioState.store(completed ? ScenarioState::COMPLETED : ScenarioState::LOADED);
    this->post(completed ? SimEvent::SCENARIO_COMPLETED : SimEvent::SCENARIO_STOPPED, "", m_scenarioPath,
               m_scenarioNext.load());
}

void Control::scenarioRun() {
    if (m_scenarioState.load() != ScenarioState::RUNNING) {
        return;
    }
    // Collect the steps that are due, then run them without holding the scenario lock so a
    // step is free to stop or reload the scenario.
    std::vector<JsonObj> due;
    bool finished = false;
    {
        std::lock_guard<std::mutex> lock(m_scenarioMutex);
        const std::uint64_t elapsed = m_tick.load() - m_scenarioStartTick;
        std::uint32_t next = m_scenarioNext.load();
        while (next < m_steps.size() && m_steps[next].tick <= elapsed) {
            due.push_back(m_steps[next].action);
            next++;
        }
        m_scenarioNext.store(next);
        finished = (next >= m_steps.size());
    }
    Bus& bus = Bus::get();
    for (const JsonObj& step : due) {
        if (step.has("op")) {
            std::string error;
            if (!this->exec(step, error)) {
                this->post(SimEvent::SCENARIO_ERROR, step.str("endpoint"), step.str("op") + ": " + error);
            }
            continue;
        }
        const std::string rec = step.str("rec");
        if (rec == "bs_rx") {
            Bytes data;
            (void)fromHex(step.str("data"), data);
            bus.byteStreamInject(step.str("endpoint"), data, "replay");
        } else if (rec == "gpio_in") {
            bus.gpioSetInput(step.str("endpoint"), step.flag("level"), "replay");
        } else if (rec == "reg_irq") {
            bus.registerInterrupt(step.str("endpoint"), static_cast<std::uint32_t>(step.unum("number")), "replay");
        }
    }
    if (finished) {
        this->scenarioStop(true);
    }
}

}  // namespace Wsts
