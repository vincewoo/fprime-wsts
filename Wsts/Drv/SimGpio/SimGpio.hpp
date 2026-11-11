// ======================================================================
// \title  SimGpio.hpp
// \brief  Simulation surrogate for a GPIO pin driver
// ======================================================================
#ifndef WSTS_SIM_GPIO_HPP
#define WSTS_SIM_GPIO_HPP

#include <string>

#include <Os/File.hpp>
#include <Os/Task.hpp>
#include <Wsts/Drv/SimGpio/SimGpioComponentAc.hpp>

namespace Wsts {

class SimGpio final : public SimGpioComponentBase {
  public:
    //! Pin configurations of Drv.LinuxGpioDriver, so topology code written against it
    //! compiles unchanged
    enum GpioConfiguration {
        GPIO_OUTPUT,
        GPIO_INPUT,
        GPIO_INTERRUPT_RISING_EDGE,
        GPIO_INTERRUPT_FALLING_EDGE,
        GPIO_INTERRUPT_BOTH_RISING_AND_FALLING_EDGES,
        MAX_GPIO_CONFIGURATION
    };

    explicit SimGpio(const char* compName);
    ~SimGpio();

    //! Bind this instance to a WSTS bus endpoint. Called from generated topology code.
    void wstsBind(const char* endpoint, const char* options = "");

    //! Stand-in for the driver's open(). The configuration selects which input edges raise
    //! the interrupt port; the device and pin arguments are accepted and ignored.
    template <typename Device, typename Pin, typename... Rest>
    Os::File::Status open(Device&&, Pin&&, GpioConfiguration configuration, Rest&&...) {
        m_configuration = configuration;
        return Os::File::OP_OK;
    }
    template <typename... Args>
    Drv::GpioStatus start(Args&&...) {
        return Drv::GpioStatus::OP_OK;
    }
    void stop() {}
    void join() {}

  private:
    Drv::GpioStatus gpioWrite_handler(FwIndexType portNum, const Fw::Logic& state) override;
    Drv::GpioStatus gpioRead_handler(FwIndexType portNum, Fw::Logic& state) override;

    //! Input level changed on the simulated pin
    void edge(bool level);
    static Drv::GpioStatus toStatus(int status);

    std::string m_endpoint;
    GpioConfiguration m_configuration;
};

}  // namespace Wsts

#endif
