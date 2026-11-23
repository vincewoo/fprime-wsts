module Components {

  @ The payload board's SpaceWire controller, as a driver for F Prime v4: F Prime's passive
  @ buffer driver interface, one packet per buffer. A buffer on bufferIn is a whole packet
  @ to transmit, its address bytes first; each packet received comes out of bufferOut in a
  @ buffer from the buffer manager.
  passive component SpwDriver {

    import Drv.PassiveBufferDriver

    @ Buffers for received packets
    output port allocate: Fw.BufferGet

    @ Return of buffers obtained through allocate
    output port deallocate: Fw.BufferSend

  }

}
