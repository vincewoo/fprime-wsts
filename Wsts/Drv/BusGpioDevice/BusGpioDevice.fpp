module Wsts {

  @ The device on a GPIO bus endpoint, offered to F Prime components as a GPIO driver: the
  @ mirror image of SimGpio. A device simulator connects to this the way it would connect
  @ to a real GPIO driver. Driving the pin (gpioWrite) drives the line towards flight
  @ software, whose input level changes and whose interrupt fires on an edge; reading it
  @ (gpioRead) reads the level flight software last drove the pin to. What flight software
  @ writes arrives on pinWrite, which the device reacts to as it would to a reset or an
  @ enable line.
  passive component BusGpioDevice {

    import Drv.Gpio

    @ Flight software drove the pin. It runs on the flight thread that drove it; the
    @ returned status is ignored
    output port pinWrite: Drv.GpioWrite

  }

}
