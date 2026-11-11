// ======================================================================
// \title  SimSetup.hpp
// \brief  Anchor component for generated simulation set-up code
// ======================================================================
#ifndef WSTS_SIM_SETUP_HPP
#define WSTS_SIM_SETUP_HPP

#include <Wsts/Svc/SimSetup/SimSetupComponentAc.hpp>

namespace Wsts {

class SimSetup final : public SimSetupComponentBase {
  public:
    explicit SimSetup(const char* compName) : SimSetupComponentBase(compName) {}
    ~SimSetup() = default;
};

}  // namespace Wsts

#endif
