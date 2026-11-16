module Wsts {

  @ One I2C transaction for the device this adapter is. It runs on the flight thread that
  @ made the transaction, with no bus lock held. The buffers wrap the transaction's own
  @ memory: the device answers by writing into readBuffer in place, and returns the status
  @ flight software sees. A write of nothing arrives as a zero-size buffer.
  port I2cDeviceTransact(
                          addr: U32 @< The I2C address of this device
                          ref writeBuffer: Fw.Buffer @< What flight software wrote
                          ref readBuffer: Fw.Buffer @< Space for the read, sized to the request
                        ) -> Drv.I2cStatus

  @ Set one register of the device directly, from the i2c_reg_set action. May stay
  @ unconnected: the action then reports that the device does not support it
  port I2cDevicePoke(
                      $reg: U8 @< The register to set
                      value: U8 @< The value to set it to
                    )

  @ The device on an I2C bus endpoint, offered to F Prime components as the I2C device they
  @ simulate. The mirror image of SimI2c: what flight software writes arrives on transact,
  @ and the answer goes back as its return value and the bytes written into readBuffer.
  @ Several of these may sit on one endpoint, each at its own address (options: "addr=<n>",
  @ required; it is the address the device answers).
  passive component BusI2cDevice {

    @ The transaction to answer
    output port transact: I2cDeviceTransact

    @ Set one register of the device (the i2c_reg_set action)
    output port poke: I2cDevicePoke

  }

}
