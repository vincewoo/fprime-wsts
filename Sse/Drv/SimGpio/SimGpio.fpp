module Sse {

  @ Simulation surrogate for a GPIO pin driver. One instance is one pin, bound to one
  @ SSE bus endpoint.
  passive component SimGpio {

    import Drv.Gpio

  }

}
