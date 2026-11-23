module Wsts {

  @ Simulation surrogate for SpaceWire drivers written to F Prime's passive buffer driver
  @ interface (Drv.PassiveBufferDriver). Each buffer flight software sends on bufferIn is one
  @ packet, put on the WSTS packet endpoint this instance is bound to, where it is routed by
  @ its address bytes to the node at its logical address. Each packet a node or an injection
  @ puts on the endpoint comes out of bufferOut, one packet per buffer.
  passive component SimSpaceWire {

    import Drv.PassiveBufferDriver

    @ Allocation for received packets. If unconnected, the surrogate allocates internally.
    output port allocate: Fw.BufferGet

    @ Return of buffers obtained through allocate
    output port deallocate: Fw.BufferSend

    @ Rate group input, present so that drivers with a telemetry tick can be replaced as-is
    sync input port run: Svc.Sched

  }

}
