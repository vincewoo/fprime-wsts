module Sse {

  @ Simulation surrogate for byte stream drivers (UART and similar).
  @ Bytes flight software sends go to the SSE bus endpoint this instance is bound to;
  @ bytes a device model or an injection puts on that endpoint come out of the recv port.
  passive component SimByteStream {

    import Drv.ByteStreamDriver

    @ Allocation for received data. If unconnected, the surrogate allocates internally.
    output port allocate: Fw.BufferGet

    @ Return of buffers obtained through allocate
    output port deallocate: Fw.BufferSend

    @ Rate group input, present so that drivers with a telemetry tick can be replaced as-is
    sync input port run: Svc.Sched

  }

}
