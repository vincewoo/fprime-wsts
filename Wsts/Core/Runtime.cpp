// ======================================================================
// \title  Runtime.cpp
// \brief  Start-up of the WSTS runtime inside a simulated deployment
// ======================================================================
#include <Wsts/Core/Runtime.hpp>

#include <atomic>
#include <thread>
#include <chrono>
#include <cstdio>
#include <cstdlib>

#include <Wsts/Core/Activity.hpp>
#include <Wsts/Core/Bridge.hpp>
#include <Wsts/Core/Control.hpp>

namespace Wsts {

namespace {

std::atomic<bool> s_started{false};

std::string env(const char* name, const std::string& fallback) {
    const char* value = std::getenv(name);
    return (value != nullptr) ? std::string(value) : fallback;
}

void run(const JsonObj& action) {
    std::string error;
    if (!Control::get().exec(action, error)) {
        std::fprintf(stderr, "[WSTS] %s failed: %s\n", action.str("op").c_str(), error.c_str());
    }
}

}  // namespace

void Runtime::start(const RuntimeConfig& config) {
    if (s_started.exchange(true)) {
        return;
    }
    Control& control = Control::get();
    const std::string stepped = env("WSTS_STEPPED", "");
    control.setStepped(stepped.empty() ? config.stepped : stepped == "1");
    const std::string epoch = env("WSTS_EPOCH", "");
    if (!epoch.empty() && std::atoll(epoch.c_str()) > 0) {
        control.setEpoch(static_cast<std::uint64_t>(std::atoll(epoch.c_str())));
    } else if (config.epoch != 0) {
        control.setEpoch(config.epoch);
    }
    if (control.stepped()) {
        // From here to the first tick the deployment's start-up code runs by itself: what
        // it sends waits on the queues, and the tasks begin on it together, in their
        // order. Start-up that waits for a task to answer would wait for ever: after a
        // while the tasks are let go, and the log says so.
        Activity::get().holdTasks(true);
        std::thread([]() {
            std::this_thread::sleep_for(std::chrono::seconds(5));
            if (Activity::get().holdingTasks()) {
                std::fprintf(stderr, "[WSTS] stepped time: no tick was asked for within 5 s of start-up; the tasks "
                                     "run now, and the order of what they do before the first tick is not fixed\n");
                Activity::get().holdTasks(false);
            }
        }).detach();
    }
    const std::string limit = env("WSTS_STEP_LIMIT_MS", "");
    if (!limit.empty() && std::atol(limit.c_str()) > 0) {
        control.setStepLimit(static_cast<std::uint32_t>(std::atol(limit.c_str())));
    }
    if (env("WSTS_START_PAUSED", "") == "1") {
        // Whoever asked (the launcher, for models that attach through the bridge) resumes
        JsonObj action;
        action.set("op", "pause");
        run(action);
    }
    (void)Bridge::get().start(env("WSTS_BRIDGE", config.bridge));

    const std::string record = env("WSTS_RECORD", "");
    if (!record.empty()) {
        JsonObj action;
        action.set("op", "record_start").set("path", record);
        run(action);
    }
    const std::string scenario = env("WSTS_SCENARIO", "");
    if (!scenario.empty()) {
        JsonObj load;
        load.set("op", "scenario_load").set("path", scenario).set("mode", env("WSTS_SCENARIO_MODE", "stimulus"));
        run(load);
        JsonObj start;
        start.set("op", "scenario_start");
        run(start);
    }
    if (!control.hasTickSource()) {
        control.startInternalTicker(config.internalTickHz);
    }
}

void Runtime::stop() {
    if (!s_started.exchange(false)) {
        return;
    }
    Control::get().stopInternalTicker();
    Control::get().stopClocks();
    JsonObj action;
    action.set("op", "record_stop");
    std::string error;
    (void)Control::get().exec(action, error);
    Bridge::get().stop();
}

}  // namespace Wsts
