// ======================================================================
// \title  Control.hpp
// \brief  WSTS simulation control core
//
// Owns simulation state (run/pause, tick, time scale), executes actions, plays scenarios
// and records bus traffic. Every front end -- the Wsts.SimControl F Prime component, the
// socket bridge and scenario files -- funnels into Control::exec with the same action
// objects, so they can all do exactly the same things.
//
// Action reference (flat JSON objects, "op" selects the action):
//   pause | resume
//   set_rate        rate
//   fault_set       endpoint kind [dir] [arg] [count] [ticks]
//   fault_clear     [endpoint]            ("*" or missing clears all)
//   inject_bytes    endpoint (data: hex | text: string)
//   gpio_force      endpoint level
//   i2c_reg_set     endpoint addr reg value
//   reg_set         endpoint offset value
//   irq_raise       endpoint [number]
//   model_set       model key value
//   record_start    path | record_stop
//   scenario_load   path [mode: stimulus|responses] | scenario_start | scenario_stop
//   log             text
// ======================================================================
#ifndef WSTS_CORE_CONTROL_HPP
#define WSTS_CORE_CONTROL_HPP

#include <atomic>
#include <condition_variable>
#include <chrono>
#include <cstdint>
#include <deque>
#include <fstream>
#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include <Wsts/Core/Bus.hpp>

namespace Wsts {

class CycleClock;

//! Base class for device models that take parameters or need a tick
class Model {
  public:
    virtual ~Model() = default;
    //! Set a named parameter. Return false if the key or value is not accepted.
    virtual bool set(const std::string& key, const std::string& value) {
        (void)key;
        (void)value;
        return false;
    }
    //! Called once per simulation tick
    virtual void onTick(std::uint64_t tick) { (void)tick; }
};

//! What arrives from another element of a system (the far end of a link). In lockstep it is
//! held until the tick it belongs to: Control delivers it at the start of each tick.
class Inbound {
  public:
    virtual ~Inbound() = default;
    //! Deliver to flight software, in the order it arrived, what was sent before `beforeNs`
    //! of simulated time. First wait, until `limit` at most, for what the conductor said will
    //! have arrived by now. Returns why that did not come, or an empty string.
    virtual std::string deliverDue(std::uint64_t beforeNs, std::chrono::steady_clock::time_point limit) = 0;
};

enum class ScenarioState : std::uint8_t { IDLE = 0, LOADED = 1, RUNNING = 2, COMPLETED = 3, FAILED = 4 };
enum class ReplayMode : std::uint8_t {
    STIMULUS = 0,   //!< Re-apply recorded actions against live models
    RESPONSES = 1,  //!< Also replay recorded device responses, with models bypassed
};

//! Something that happened in the simulation, surfaced as WSTS events
struct SimEvent {
    enum Code : std::uint8_t {
        PAUSED,
        RESUMED,
        RATE_SET,
        FAULT_ARMED,
        FAULT_TRIGGERED,
        FAULT_EXPIRED,
        FAULTS_CLEARED,
        SCENARIO_LOADED,
        SCENARIO_STARTED,
        SCENARIO_COMPLETED,
        SCENARIO_STOPPED,
        SCENARIO_ERROR,
        RECORD_STARTED,
        RECORD_STOPPED,
        INJECTED,
        ACTION_FAILED,
        LOG,
        UNANSWERED,  //!< flight software's first access nothing answered, per endpoint and address
    };
    Code code = LOG;
    std::string endpoint;
    std::string text;
    std::uint32_t a = 0;
    std::uint32_t b = 0;
    JsonObj toJson() const;
};

struct SimStatus {
    std::uint64_t tick = 0;
    bool paused = false;
    std::uint64_t stepping = 0;  //!< ticks left of a `step`, after which the simulation pauses
    bool counting = false;       //!< the deployment's queues count outstanding work (Wsts_Os_Queue)
    std::uint64_t activity = 0;  //!< work flight software has been given and not finished
    std::uint64_t work = 0;      //!< pieces of work since start-up
    std::uint64_t busyTicks = 0; //!< ticks taken while work of an earlier one was outstanding
    std::uint64_t lateTicks = 0; //!< real time: ticks taken after the next was already due (the host fell behind)
    bool stepped = false;        //!< stepped time: a tick is taken when the last one is finished
    bool lockstep = false;       //!< lockstep: a tick is taken when the system's conductor grants one
    bool settled = false;        //!< stepped time: start-up has settled, the first tick may be taken
    std::uint64_t tickPeriodNs = 0; //!< simulated time one tick covers; 0 while not known
    double simSeconds = 0.0;     //!< simulated time: ticks times the tick period
    std::string stuck;           //!< stepped time: where the work of a tick that did not finish is
    double rate = 1.0;
    std::uint32_t activeFaults = 0;
    ScenarioState scenarioState = ScenarioState::IDLE;
    std::uint32_t scenarioStep = 0;
    std::uint32_t scenarioSteps = 0;
    bool recording = false;
    BusCounters counters;
    JsonObj toJson() const;
};

//! Stepped time: how long ticks took in host time, from onTick() until finishTick() found
//! their work finished (docs/stepped-time.md, decision 4). A hint at what would overrun on
//! the target, where stepped time hides it: never a failure, and never seen by flight
//! software, so two runs stay the same.
struct TickTimes {
    static constexpr std::size_t SLOWEST = 5;  //!< ticks kept in `slowest`
    struct Slow {
        std::uint64_t tick = 0;
        std::uint64_t ns = 0;
    };
    std::uint64_t ticks = 0;       //!< ticks timed: those taken in stepped time that finished
    std::uint64_t totalNs = 0;
    std::uint64_t maxNs = 0;
    std::uint64_t overPeriod = 0;  //!< ticks that took longer than the tick period
    std::vector<Slow> slowest;     //!< the slowest ticks, slowest first; the earlier tick of two equal ones
    void add(std::uint64_t tick, std::uint64_t ns, std::uint64_t periodNs);
    //! Flat, as every bridge message is: `ticks`, `mean_us`, `max_us`, `over_period`, and
    //! `slowest.<i>.tick`, `slowest.<i>.us` (fprime_wsts.bridge.tick_times nests them)
    void addTo(JsonObj& obj) const;
};

class Control : public BusObserver {
  public:
    using TickListener = std::function<void(std::uint64_t tick, bool paused)>;
    using RecordTap = std::function<void(const JsonObj&)>;
    using EventTap = std::function<void(const SimEvent&)>;

    static Control& get();

    // ---- Time ----

    //! Advance the simulation by one tick. Called by the tick source (Wsts.SimTick or the
    //! internal ticker) before it cycles flight software.
    void onTick();
    //! Called by the tick source instead of onTick while paused, so listeners keep running
    void onIdle();
    std::uint64_t tick() const { return m_tick.load(); }
    bool paused() const { return m_paused.load(); }
    double rate() const { return m_rate.load(); }
    // ---- Stepped time (docs/stepped-time.md) ----
    //
    // In stepped time the wall clock does not decide when a tick is taken: the next one is
    // taken when flight software has finished what the last one caused. A tick source
    // brackets each tick with awaitTick() and finishTick(); Wsts.SimTick and the internal
    // ticker do. Actions from outside (the bridge) run between two ticks, never inside one.

    using Clock = std::chrono::steady_clock;
    static constexpr std::uint64_t DEFAULT_EPOCH = 946684800ull;  //!< 2000-01-01T00:00:00Z
    bool stepped() const { return m_stepped.load(); }
    void setStepped(bool stepped);
    //! For a tick source: wait until the next tick is due. `next` is the source's own
    //! schedule, which this advances. Returns false when no tick is to be taken now (the
    //! simulation is paused, or `quit` became true): call onIdle() and ask again.
    bool awaitTick(std::uint64_t periodUs, Clock::time_point& next, const std::atomic<bool>& quit);
    //! For a tick source, after onTick() and after it has cycled flight software: in
    //! stepped time, wait until the work of the tick is finished.
    void finishTick();
    //! Simulated time: the epoch plus the ticks taken so far, in nanoseconds
    std::uint64_t simTimeNs() const;
    //! Simulated time since start-up: the ticks taken so far times the tick period
    std::uint64_t elapsedNs() const;
    //! Where simulated time starts, in seconds since 1970 (default: 2000-01-01T00:00:00Z)
    void setEpoch(std::uint64_t seconds) { m_epochSeconds.store(seconds); }
    std::uint64_t epoch() const { return m_epochSeconds.load(); }
    //! The time flight software is given: simulated time in stepped time, else the host's clock
    void flightTime(std::uint32_t& seconds, std::uint32_t& microseconds) const;
    //! Longest a tick may take to finish in stepped time before it is reported and the
    //! simulation pauses (wall-clock milliseconds)
    void setStepLimit(std::uint32_t milliseconds) { m_stepLimitMs.store(milliseconds); }
    //! Where the work of a tick that did not finish is; empty when none has been stuck
    std::string stuck();
    //! How long the ticks taken in stepped time took in host time
    TickTimes tickTimes();

    //! Held around an action that comes from outside the simulation (a bridge client). In
    //! stepped time it waits for the tick being taken to finish, keeps the next one from
    //! starting, and lets go when what the action caused is finished. Nothing in real time.
    class ActionGate {
      public:
        ActionGate();
        ~ActionGate();
        ActionGate(const ActionGate&) = delete;
        ActionGate& operator=(const ActionGate&) = delete;

      private:
        bool m_held;
    };

    // ---- Lockstep (docs/stepped-time.md, phase 4) ----
    //
    // The elements of a system are stepped together by a conductor (fprime-wsts run, or a
    // WstsSystem client) that advances simulated time instant by instant. An element in
    // lockstep takes a tick only when the conductor grants one, and what its links bring
    // in is held until the first tick later than the time it was sent at.

    bool lockstep() const { return m_lockstep.load(); }
    //! Lockstep needs stepped time: turning it on turns that on too. Turning it off delivers
    //! whatever the links hold.
    void setLockstep(bool lockstep);
    //! Grant ticks (the conductor). False, and nothing granted, when not in lockstep.
    bool grant(std::uint64_t ticks);
    //! Wait until the ticks granted are taken and finished. False if the time came first.
    bool awaitGranted(std::chrono::milliseconds timeout);
    void addInbound(Inbound* inbound);
    void removeInbound(Inbound* inbound);

    //! Mark that a surrogate tick source exists, so no internal ticker is needed
    void registerTickSource() { m_hasTickSource.store(true); }
    bool hasTickSource() const { return m_hasTickSource.load(); }
    //! Start a wall-clock ticker thread (deployments without a substituted tick source)
    void startInternalTicker(std::uint32_t hz);
    void stopInternalTicker();

    //! Simulated time one tick covers, in nanoseconds. A tick source that knows its period
    //! says so (Wsts.SimTick and the internal ticker do); otherwise the core measures the
    //! interval between ticks. Cycle clocks are fed from this.
    void setTickPeriod(std::uint64_t nanoseconds) { m_tickPeriodNs.store(nanoseconds); }
    //! The declared period, else the measured one, else 0 while it is not known yet
    std::uint64_t tickPeriodNs() const;
    //! Cycle sources with a rate of their own (see Clock.hpp). They are granted their
    //! cycles on each tick, so they stop while paused.
    void addClock(CycleClock* clock);
    void removeClock(CycleClock* clock);
    //! Stop every registered clock (runtime shutdown)
    void stopClocks();

    // ---- Actions ----

    //! Execute one action. Returns false and fills `error` on failure.
    bool exec(const JsonObj& action, std::string& error);
    //! Parse and execute one JSON action line
    bool execLine(const std::string& line, std::string& error);

    // ---- Models ----

    void addModel(const std::string& name, Model* model);

    // ---- Observation ----

    SimStatus status();
    //! Next queued event for the F Prime front end. False when the queue is empty.
    bool popEvent(SimEvent& event);
    void addTickListener(TickListener listener);
    //! Something that writes telemetry of its own when the WSTS telemetry is published (the
    //! channels of the models' registers): called by Wsts.SimControl, on its thread
    void addTelemetrySource(std::function<void()> source);
    //! Call every telemetry source (Wsts.SimControl, after writing its own channels)
    void publishTelemetrySources();
    //! Bus records and executed actions, as JSON (bridge subscribers)
    void setRecordTap(RecordTap tap);
    void setEventTap(EventTap tap);

    // ---- BusObserver ----

    void onRecord(const Record& record) override;
    void onFaultHit(const Fault& fault, bool first, bool expired) override;

    //! Return to the initial state and reset the bus (unit tests)
    void reset();

  private:
    struct Step {
        std::uint64_t tick;
        JsonObj action;
    };

    Control();
    void post(SimEvent::Code code,
              const std::string& endpoint = "",
              const std::string& text = "",
              std::uint32_t a = 0,
              std::uint32_t b = 0);
    bool dispatch(const JsonObj& action, std::string& error);
    bool faultSet(const JsonObj& action, std::string& error);
    bool injectBytes(const JsonObj& action, std::string& error);
    bool recordStart(const std::string& path, std::string& error);
    void recordStop();
    void recordLine(JsonObj line);
    bool scenarioLoad(const std::string& path, ReplayMode mode, std::string& error);
    bool scenarioStart(std::string& error);
    void scenarioStop(bool completed);
    void scenarioRun();
    void notifyListeners(bool paused);
    void grantClocks();
    bool waitFinished(Clock::time_point limit);
    //! Wait until nothing is outstanding and every task that was started has come to its
    //! queue (Activity::tasksSettled()). False if the limit came first.
    bool waitSettled(Clock::time_point limit);
    struct PendingCycle {
        std::uint64_t offsetNs;  //!< from the start of the tick
        std::size_t order;       //!< of the clock, among the clocks
        CycleClock* clock;
    };
    std::vector<PendingCycle> m_cycles;  //!< stepped time: granted by this tick, to issue (tick thread only)

    std::atomic<std::uint64_t> m_tick{0};
    std::atomic<bool> m_paused{false};
    std::atomic<bool> m_stepped{false};
    std::atomic<bool> m_lockstep{false};
    std::atomic<double> m_steppedRate{0.0};  //!< pace of stepped time against the wall clock; 0: as fast as it goes
    std::atomic<std::uint32_t> m_stepLimitMs{10000};
    std::atomic<bool> m_startedUp{false};  //!< stepped time: the tasks were let go before the first tick
    std::atomic<bool> m_settled{false};    //!< ... and had settled
    std::atomic<std::uint64_t> m_epochSeconds{DEFAULT_EPOCH};
    std::mutex m_gateMutex;  //!< the three below, and m_stuck
    std::condition_variable m_gate;
    bool m_tickOpen = false;       //!< a tick is being taken and has not finished
    bool m_actionOpen = false;     //!< an action from outside is being applied
    std::uint32_t m_actionsWaiting = 0;
    std::uint64_t m_granted = 0;   //!< lockstep: ticks granted and not taken yet
    std::string m_stuck;
    std::string m_late;            //!< lockstep: what a link did not bring in time for this tick (tick thread)
    std::mutex m_inboundMutex;
    std::vector<Inbound*> m_inbound;
    std::atomic<std::uint64_t> m_busyTicks{0};  //!< ticks taken before the work of the last one was finished
    std::atomic<std::uint64_t> m_lateTicks{0};  //!< real time: ticks taken after the next was already due
    Clock::time_point m_tickStarted;  //!< stepped time: when onTick() began this tick (tick thread only)
    bool m_tickTimed = false;         //!< ... and it is to be timed (tick thread only)
    std::mutex m_tickTimesMutex;
    TickTimes m_tickTimes;
    //! Real time: the last awaitTick() let a tick be taken (a tick after a pause is behind
    //! its schedule, and is not late)
    std::atomic<bool> m_lastTickTaken{false};
    std::atomic<std::uint64_t> m_stepUntil{0};  //!< tick at which a `step` pauses; 0: not stepping
    std::atomic<double> m_rate{1.0};
    std::atomic<bool> m_hasTickSource{false};

    std::mutex m_mutex;  //!< models, events, listeners, taps
    std::map<std::string, Model*> m_models;
    std::deque<SimEvent> m_events;
    //! Endpoints and addresses an UNANSWERED event has been posted for
    std::set<std::pair<std::string, std::uint64_t>> m_unanswered;
    std::vector<TickListener> m_listeners;
    std::vector<std::function<void()>> m_telemetrySources;
    RecordTap m_recordTap;
    EventTap m_eventTap;

    std::mutex m_recordMutex;
    std::ofstream m_recordFile;
    std::atomic<bool> m_recording{false};
    std::uint64_t m_recordStartTick = 0;
    std::uint32_t m_recordCount = 0;

    std::mutex m_scenarioMutex;
    std::vector<Step> m_steps;
    std::atomic<ScenarioState> m_scenarioState{ScenarioState::IDLE};
    ReplayMode m_scenarioMode = ReplayMode::STIMULUS;
    std::uint64_t m_scenarioStartTick = 0;
    std::atomic<std::uint32_t> m_scenarioNext{0};
    std::atomic<std::uint32_t> m_scenarioSize{0};
    std::string m_scenarioPath;

    std::atomic<std::uint64_t> m_tickPeriodNs{0};   //!< declared by the tick source
    std::atomic<std::uint64_t> m_measuredPeriodNs{0};
    std::chrono::steady_clock::time_point m_lastTick;
    bool m_lastTickValid = false;  //!< tick thread only
    std::mutex m_clockMutex;
    std::vector<CycleClock*> m_clocks;

    std::thread m_ticker;
    std::atomic<bool> m_tickerQuit{false};
};

// ----------------------------------------------------------------------
// Helpers for generated set-up code. A device class may or may not derive from Model:
// overload resolution picks the Model* overloads when it does (derived-to-base beats
// conversion to void*).
// ----------------------------------------------------------------------

inline void registerModel(const std::string& name, Model* model) {
    Control::get().addModel(name, model);
}
inline void registerModel(const std::string& name, void* model) {
    (void)name;
    (void)model;
}
//! Set a model parameter at start-up. Returns false if the model rejected it.
inline bool configureModel(Model* model, const std::string& key, const std::string& value) {
    return model->set(key, value);
}
inline bool configureModel(void* model, const std::string& key, const std::string& value) {
    (void)model;
    (void)key;
    (void)value;
    return false;
}

}  // namespace Wsts

#endif
