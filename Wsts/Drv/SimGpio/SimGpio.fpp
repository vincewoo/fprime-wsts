module Wsts {

  @ Simulation surrogate for a GPIO pin driver. One instance is one pin, bound to one
  @ WSTS bus endpoint.
  passive component SimGpio {

    import Drv.Gpio

  }

}
