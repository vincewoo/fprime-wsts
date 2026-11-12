module SensorSim {

  @ A sensor model that has to be stepped faster than any flight rate group runs.
  @ It is scheduled by a rate group of the simulation, behind a Wsts.SimCycle.
  passive component GyroSim {

    @ One model step per call
    sync input port schedIn: Svc.Sched

    @ Model steps taken
    telemetry Steps: U32

    @ Simulated seconds the model has covered, from its step count and step rate
    telemetry ModelTime: F32

    @ The motion profile in use
    telemetry Profile: SimCommon.Motion

    @ The motion profile was chosen at start-up
    event ProfileLoaded(motion: SimCommon.Motion, source: string size 80) \
      severity activity high format "Gyro simulator profile {} from {}"

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller

  }

}
