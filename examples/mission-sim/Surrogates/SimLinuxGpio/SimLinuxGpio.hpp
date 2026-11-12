// ======================================================================
// \title  SimLinuxGpio.hpp
// \brief  Mirror surrogate for Drv.LinuxGpioDriver
// ======================================================================
#ifndef SIM_SIM_LINUX_GPIO_HPP
#define SIM_SIM_LINUX_GPIO_HPP

#include <string>

#include <Os/File.hpp>
#include <Os/Task.hpp>
#include <Surrogates/SimLinuxGpio/SimLinuxGpioComponentAc.hpp>

namespace Sim {

//! Stands in for Drv::LinuxGpioDriver. The pin is a WSTS bus endpoint; the component has
//! the flight driver's events, and emits OpenChip as the flight driver does.
class SimLinuxGpio final : public SimLinuxGpioComponentBase {
  public:
    //! Pin configurations of Drv::LinuxGpioDriver, so topology code compiles unchanged
    enum GpioConfiguration {
        GPIO_OUTPUT,
        GPIO_INPUT,
        GPIO_INTERRUPT_RISING_EDGE,
        GPIO_INTERRUPT_FALLING_EDGE,
        GPIO_INTERRUPT_BOTH_RISING_AND_FALLING_EDGES,
        MAX_GPIO_CONFIGURATION
    };

    explicit SimLinuxGpio(const char* compName);
    ~SimLinuxGpio();

    //! Bind this instance to a WSTS bus endpoint. Called from generated topology code.
    void wstsBind(const char* endpoint, const char* options = "");

    //! Stand-in for Drv::LinuxGpioDriver::open(): records the configuration and reports
    //! the open as the flight driver does
    Os::File::Status open(const char* device, U32 gpio, GpioConfiguration configuration);

    //! The flight driver starts a thread that waits for interrupts; edges arrive from the
    //! bus here, so there is nothing to start
    Drv::GpioStatus start(FwTaskPriorityType priority = Os::Task::TASK_PRIORITY_DEFAULT,
                          FwSizeType stackSize = Os::Task::TASK_DEFAULT,
                          FwSizeType cpuAffinity = Os::Task::TASK_DEFAULT,
                          FwTaskIdType identifier = static_cast<FwTaskIdType>(Os::Task::TASK_DEFAULT)) {
        (void)priority;
        (void)stackSize;
        (void)cpuAffinity;
        (void)identifier;
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

}  // namespace Sim

#endif
