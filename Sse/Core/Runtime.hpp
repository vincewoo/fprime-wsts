// ======================================================================
// \title  Runtime.hpp
// \brief  Start-up of the SSE runtime inside a simulated deployment
//
// Called from generated set-up code (SseSetup.cpp) once models are attached.
// Environment variables override the generated defaults so that a run can be
// reconfigured without rebuilding:
//   SSE_BRIDGE    bridge address ("tcp:<port>", "unix:<path>", "none")
//   SSE_SCENARIO  scenario file to load and start immediately
//   SSE_SCENARIO_MODE  "stimulus" (default) or "responses"
//   SSE_RECORD    file to record bus traffic and actions to, from start-up
//   SSE_DIR       directory that relative scenario and recording paths are resolved against
// ======================================================================
#ifndef SSE_CORE_RUNTIME_HPP
#define SSE_CORE_RUNTIME_HPP

#include <string>

namespace Sse {

struct RuntimeConfig {
    std::string bridge = "none";   //!< Default bridge address
    unsigned internalTickHz = 10;  //!< Ticker rate used when no surrogate tick source exists
};

class Runtime {
  public:
    //! Start the bridge, the fallback ticker and any scenario/recording requested through the
    //! environment. Safe to call more than once; only the first call has an effect.
    static void start(const RuntimeConfig& config);
    static void stop();
};

}  // namespace Sse

#endif
