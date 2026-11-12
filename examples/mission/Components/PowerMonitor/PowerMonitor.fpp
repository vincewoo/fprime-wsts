module Components {

  @ Device manager for a register-addressed I2C power monitor (an INA219-style part) that
  @ shares the I2C bus with the IMU
  passive component PowerMonitor {

    @ I2C bus the power monitor is on
    output port i2cWriteRead: Drv.I2cWriteRead

    @ Rate group input: sample the power monitor
    sync input port run: Svc.Sched

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller

    @ An I2C transaction with the power monitor failed
    event PowerReadError(status: Drv.I2cStatus) \
      severity warning low format "Power monitor read failed: {}" \
      throttle 5

    @ Bus voltage, in volts
    telemetry BusVoltage: F32

    @ Bus current, in milliamperes
    telemetry Current: I16

  }

}
