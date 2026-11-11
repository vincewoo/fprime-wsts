module Sse {

  @ Simulation tick source. Replaces hardware or OS timers (Svc.LinuxTimer, a VxWorks
  @ watchdog timer, ...) and advances SSE simulation time before each flight cycle.
  passive component SimTick {

    import Drv.Tick

  }

}
