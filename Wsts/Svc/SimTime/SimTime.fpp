module Wsts {

  @ The time source of a simulated deployment. In real time it gives the workstation's
  @ clock, as Svc.PosixTime and Svc.ChronoTime do. In stepped time it gives simulated
  @ time: an epoch plus the ticks taken so far, so that two runs stamp their events and
  @ telemetry alike.
  passive component SimTime {

    import Svc.Time

  }

}
