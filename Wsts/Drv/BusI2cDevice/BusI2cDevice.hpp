// ======================================================================
// \title  BusI2cDevice.hpp
// \brief  Adapter from a WSTS I2C endpoint to an F Prime device simulator
// ======================================================================
#ifndef WSTS_BUS_I2C_DEVICE_HPP
#define WSTS_BUS_I2C_DEVICE_HPP

#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Drv/BusI2cDevice/BusI2cDeviceComponentAc.hpp>

namespace Wsts {

class BusI2cDevice final : public BusI2cDeviceComponentBase, public I2cDevice {
  public:
    explicit BusI2cDevice(const char* compName);
    ~BusI2cDevice();

    //! Become the device at an I2C address on a bus endpoint. Called by generated set-up
    //! code, after the surrogates are bound. Options: "addr=<n>", the address the device
    //! answers (required; the generator insists on it as well)
    void wstsServe(const char* endpoint, const char* options = "");

    //! Bus side: one transaction. Runs on the flight thread that made it, no bus lock held.
    //! 0 on success, otherwise a Drv::I2cStatus value
    int transact(std::uint32_t addr, const Bytes& write, Bytes& read) override;

    //! Bus side: the i2c_reg_set action. True if the simulator takes it
    bool pokeRegister(std::uint8_t reg, std::uint8_t value) override;

  private:
    std::string m_endpoint;
    std::uint32_t m_addr;
};

}  // namespace Wsts

#endif
