// ======================================================================
// \title  BusSpiDevice.hpp
// \brief  Adapter from a WSTS SPI endpoint to an F Prime device simulator
// ======================================================================
#ifndef WSTS_BUS_SPI_DEVICE_HPP
#define WSTS_BUS_SPI_DEVICE_HPP

#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Drv/BusSpiDevice/BusSpiDeviceComponentAc.hpp>

namespace Wsts {

class BusSpiDevice final : public BusSpiDeviceComponentBase, public SpiDevice {
  public:
    explicit BusSpiDevice(const char* compName);
    ~BusSpiDevice();

    //! Become the device on a bus endpoint. Called by generated set-up code, after the
    //! surrogates are bound. No options
    void wstsServe(const char* endpoint, const char* options = "");

    //! Bus side: one transfer. Runs on the flight thread that made it, no bus lock held.
    //! 0 on success, otherwise a Drv::SpiStatus value
    int transfer(const Bytes& write, Bytes& read) override;

  private:
    std::string m_endpoint;
};

}  // namespace Wsts

#endif
