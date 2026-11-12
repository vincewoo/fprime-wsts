module SensorSim {

  @ Stand-in for the flight time source in simulation. It has the flight component's
  @ port and nothing else: it does not use the WSTS bus, so the manifest substitutes it
  @ with `endpoint: none`.
  passive component SimTime {

    import Svc.Time

  }

}
