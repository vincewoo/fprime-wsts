#ifndef COMPONENTS_ADC_HPP
#define COMPONENTS_ADC_HPP

#include "Components/Adc/AdcComponentAc.hpp"

namespace Components {

class Adc final : public AdcComponentBase {
  public:
    static constexpr U8 DETECTOR_CHANNEL = 0;

    explicit Adc(const char* compName);
    ~Adc() = default;

  private:
    void run_handler(FwIndexType portNum, U32 context) override;
};

}  // namespace Components

#endif
