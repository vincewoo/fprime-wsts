module Wsts {

  @ The device on a byte stream bus endpoint, offered to F Prime components as a byte
  @ stream driver. A device simulator written as a component (a client of
  @ Drv.ByteStreamDriver) connects to it as it would to a UART driver: what flight
  @ software sends on the endpoint comes out of the recv port, after faults are applied,
  @ and what the simulator sends goes to flight software as the device's reply.
  passive component BusByteStreamDevice {

    import Drv.ByteStreamDriver

    @ Allocation for data delivered on recv. If unconnected, the adapter allocates internally.
    output port allocate: Fw.BufferGet

    @ Return of buffers obtained through allocate
    output port deallocate: Fw.BufferSend

  }

}
