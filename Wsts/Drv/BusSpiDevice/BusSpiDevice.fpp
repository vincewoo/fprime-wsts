module Wsts {

  @ One SPI transfer for the device this adapter is. It runs on the flight thread that made
  @ the transfer, with no bus lock held. The buffers wrap the transfer's own memory: the
  @ device answers by writing into readBuffer in place, and returns the status flight
  @ software sees. An endpoint is one chip select, so one of these sits on each endpoint.
  port SpiDeviceTransfer(
                          ref writeBuffer: Fw.Buffer @< What flight software wrote
                          ref readBuffer: Fw.Buffer @< Space for the read, sized to the request
                        ) -> Drv.SpiStatus

  @ The device on an SPI bus endpoint, offered to F Prime components as the SPI device they
  @ simulate. The mirror image of SimSpi: what flight software sends arrives on transfer,
  @ and the answer goes back as its return value and the bytes written into readBuffer.
  passive component BusSpiDevice {

    @ The transfer to answer
    output port transfer: SpiDeviceTransfer

  }

}
