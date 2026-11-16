// ======================================================================
// \title  BusGpioDevice.hpp
// \brief  Adapter from a WSTS GPIO endpoint to an F Prime device simulator
// ======================================================================
#ifndef WSTS_BUS_GPIO_DEVICE_HPP
#define WSTS_BUS_GPIO_DEVICE_HPP

#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Drv/BusGpioDevice/BusGpioDeviceComponentAc.hpp>

namespace Wsts {

class BusGpioDevice final : public BusGpioDeviceComponentBase, public GpioDevice {
  public:
    explicit BusGpioDevice(const char* compName);
    ~BusGpioDevice();

    //! Become the device on a bus endpoint. Called by generated set-up code, after the
    //! surrogates are bound. No options
    void wstsServe(const char* endpoint, const char* options = "");

    //! Bus side: flight software drove the pin. Runs on the thread that drove it
    void onWrite(bool level) override;

  private:
    Drv::GpioStatus gpioWrite_handler(FwIndexType portNum, const Fw::Logic& state) override;
    Drv::GpioStatus gpioRead_handler(FwIndexType portNum, Fw::Logic& state) override;

    std::string m_endpoint;
};

}  // namespace Wsts

#endif
