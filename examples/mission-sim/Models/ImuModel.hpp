// ======================================================================
// \title  ImuModel.hpp
// \brief  Device model of the Payload IMU (an MPU-6050-style register map)
// ======================================================================
#ifndef SIM_IMU_MODEL_HPP
#define SIM_IMU_MODEL_HPP

#include <array>
#include <cstdint>
#include <mutex>
#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>

namespace Sim {

//! An I2C IMU whose acceleration is set in g rather than as raw register bytes.
//!
//! Parameters (manifest `params`, model_set, MODEL_SET):
//!   accel_x, accel_y, accel_z   acceleration in g (default 0, 0, 1)
//!   who_am_i                    identity register value (default 0x68)
class ImuModel : public Wsts::Model, public Wsts::I2cDevice {
  public:
    static constexpr std::uint8_t REG_ACCEL_XOUT_H = 0x3B;
    static constexpr std::uint8_t REG_WHO_AM_I = 0x75;
    static constexpr double LSB_PER_G = 16384.0;  // +/-2 g full scale

    ImuModel();

    bool set(const std::string& key, const std::string& value) override;
    int transact(std::uint32_t addr, const Wsts::Bytes& write, Wsts::Bytes& read) override;
    bool pokeRegister(std::uint8_t reg, std::uint8_t value) override;

  private:
    void writeAxis(int axis, double g);

    std::mutex m_mutex;
    std::array<std::uint8_t, 256> m_registers{};
    std::uint8_t m_pointer = 0;
};

}  // namespace Sim

#endif
