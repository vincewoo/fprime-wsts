// ======================================================================
// \title  SimSetup.hpp
// \brief  Anchor component for generated simulation set-up code
// ======================================================================
#ifndef SSE_SIM_SETUP_HPP
#define SSE_SIM_SETUP_HPP

#include <Sse/Svc/SimSetup/SimSetupComponentAc.hpp>

namespace Sse {

class SimSetup final : public SimSetupComponentBase {
  public:
    explicit SimSetup(const char* compName) : SimSetupComponentBase(compName) {}
    ~SimSetup() = default;
};

}  // namespace Sse

#endif
