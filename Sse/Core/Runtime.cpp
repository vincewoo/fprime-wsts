// ======================================================================
// \title  Runtime.cpp
// \brief  Start-up of the SSE runtime inside a simulated deployment
// ======================================================================
#include <Sse/Core/Runtime.hpp>

#include <atomic>
#include <cstdio>
#include <cstdlib>

#include <Sse/Core/Bridge.hpp>
#include <Sse/Core/Control.hpp>

namespace Sse {

namespace {

std::atomic<bool> s_started{false};

std::string env(const char* name, const std::string& fallback) {
    const char* value = std::getenv(name);
    return (value != nullptr) ? std::string(value) : fallback;
}

void run(const JsonObj& action) {
    std::string error;
    if (!Control::get().exec(action, error)) {
        std::fprintf(stderr, "[SSE] %s failed: %s\n", action.str("op").c_str(), error.c_str());
    }
}

}  // namespace

void Runtime::start(const RuntimeConfig& config) {
    if (s_started.exchange(true)) {
        return;
    }
    Control& control = Control::get();
    (void)Bridge::get().start(env("SSE_BRIDGE", config.bridge));

    const std::string record = env("SSE_RECORD", "");
    if (!record.empty()) {
        JsonObj action;
        action.set("op", "record_start").set("path", record);
        run(action);
    }
    const std::string scenario = env("SSE_SCENARIO", "");
    if (!scenario.empty()) {
        JsonObj load;
        load.set("op", "scenario_load").set("path", scenario).set("mode", env("SSE_SCENARIO_MODE", "stimulus"));
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
    JsonObj action;
    action.set("op", "record_stop");
    std::string error;
    (void)Control::get().exec(action, error);
    Bridge::get().stop();
}

}  // namespace Sse
