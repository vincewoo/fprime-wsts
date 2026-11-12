module Wsts {

  @ Cycle source for simulator components that need a rate of their own: faster than
  @ any flight rate group, or not a multiple of the flight tick. Connect CycleOut to a
  @ rate group driver or a rate group of the simulation. The rate is given in the
  @ manifest (`clocks`). The cycles follow simulation time: they stop while the
  @ simulation is paused and scale with its rate.
  passive component SimCycle {

    @ One call per cycle, on this instance's own thread
    output port CycleOut: Svc.Cycle

  }

}
