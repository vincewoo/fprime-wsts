module Wsts {

  @ Simulation surrogate for I2C bus drivers. Transactions are answered by the device
  @ model attached to the WSTS bus endpoint at the addressed I2C address.
  passive component SimI2c {

    import Drv.I2c

  }

}
