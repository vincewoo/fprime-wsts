// ======================================================================
// \title  Control.hpp
// \brief  SSE simulation control core
//
// Owns simulation state (run/pause, tick, time scale), executes actions, plays scenarios
// and records bus traffic. Every front end -- the Sse.SimControl F Prime component, the
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
//   model_set       model key value
//   record_start    path | record_stop
//   scenario_load   path [mode: stimulus|responses] | scenario_start | scenario_stop
//   log             text
// ======================================================================
#ifndef SSE_CORE_CONTROL_HPP
#define SSE_CORE_CONTROL_HPP

#include <atomic>
#include <cstdint>
#include <deque>
#include <fstream>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <Sse/Core/Bus.hpp>

namespace Sse {

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

enum class ScenarioState : std::uint8_t { IDLE = 0, LOADED = 1, RUNNING = 2, COMPLETED = 3, FAILED = 4 };
enum class ReplayMode : std::uint8_t {
    STIMULUS = 0,   //!< Re-apply recorded actions against live models
    RESPONSES = 1,  //!< Also replay recorded device responses, with models bypassed
};

//! Something that happened in the simulation, surfaced as SSE events
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
    double rate = 1.0;
    std::uint32_t activeFaults = 0;
    ScenarioState scenarioState = ScenarioState::IDLE;
    std::uint32_t scenarioStep = 0;
    std::uint32_t scenarioSteps = 0;
    bool recording = false;
    BusCounters counters;
    JsonObj toJson() const;
};

class Control : public BusObserver {
  public:
    using TickListener = std::function<void(std::uint64_t tick, bool paused)>;
    using RecordTap = std::function<void(const JsonObj&)>;
    using EventTap = std::function<void(const SimEvent&)>;

    static Control& get();

    // ---- Time ----

    //! Advance the simulation by one tick. Called by the tick source (Sse.SimTick or the
    //! internal ticker) before it cycles flight software.
    void onTick();
    //! Called by the tick source instead of onTick while paused, so listeners keep running
    void onIdle();
    std::uint64_t tick() const { return m_tick.load(); }
    bool paused() const { return m_paused.load(); }
    double rate() const { return m_rate.load(); }
    //! Mark that a surrogate tick source exists, so no internal ticker is needed
    void registerTickSource() { m_hasTickSource.store(true); }
    bool hasTickSource() const { return m_hasTickSource.load(); }
    //! Start a wall-clock ticker thread (deployments without a substituted tick source)
    void startInternalTicker(std::uint32_t hz);
    void stopInternalTicker();

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

    std::atomic<std::uint64_t> m_tick{0};
    std::atomic<bool> m_paused{false};
    std::atomic<double> m_rate{1.0};
    std::atomic<bool> m_hasTickSource{false};

    std::mutex m_mutex;  //!< models, events, listeners, taps
    std::map<std::string, Model*> m_models;
    std::deque<SimEvent> m_events;
    std::vector<TickListener> m_listeners;
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

}  // namespace Sse

#endif
