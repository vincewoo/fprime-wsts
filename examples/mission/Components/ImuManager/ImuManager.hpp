#ifndef COMPONENTS_IMU_MANAGER_HPP
#define COMPONENTS_IMU_MANAGER_HPP

#include "Components/ImuManager/ImuManagerComponentAc.hpp"

namespace Components {

class ImuManager final : public ImuManagerComponentBase {
  public:
    static constexpr U32 I2C_ADDRESS = 0x68;
    static constexpr U8 REG_ACCEL_XOUT_H = 0x3B;
    static constexpr U8 REG_WHO_AM_I = 0x75;

    explicit ImuManager(const char* compName);
    ~ImuManager() = default;

  private:
    void run_handler(FwIndexType portNum, U32 context) override;

    //! Read `size` bytes starting at register `reg`
    Drv::I2cStatus readRegisters(U8 reg, U8* data, U32 size);

    bool m_detected;
    bool m_failing;
    U32 m_errors;
    U32 m_consecutiveErrors;
};

}  // namespace Components

#endif
