module Components {

  @ Switches a heater through a GPIO output and watches an over-temperature switch on a
  @ GPIO input. The heater is turned off as soon as the switch trips.
  passive component HeaterControl {

    @ Heater enable pin
    output port heaterWrite: Drv.GpioWrite

    @ Over-temperature switch pin
    output port switchRead: Drv.GpioRead

    @ Interrupt from the over-temperature switch pin
    sync input port switchTripped: Svc.Cycle

    @ Rate group input: sample the switch
    sync input port run: Svc.Sched

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut

    @ Turn the heater on or off
    sync command HEATER_SET($state: Fw.On)

    event HeaterSet($state: Fw.On) \
      severity activity high format "Heater turned {}"

    event HeaterWriteError(status: Drv.GpioStatus) \
      severity warning high format "Heater pin write failed: {}"

    event OverTemperature \
      severity warning high format "Over-temperature switch tripped: heater turned off"

    telemetry HeaterState: Fw.On
    telemetry SwitchState: Fw.Logic
    telemetry TripCount: U32

  }

}
