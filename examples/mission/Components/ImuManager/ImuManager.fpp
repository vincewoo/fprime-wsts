module Components {

  @ Device manager for a register-addressed I2C inertial measurement unit
  passive component ImuManager {

    @ I2C bus the IMU is on
    output port i2cWriteRead: Drv.I2cWriteRead

    @ Rate group input: sample the IMU
    sync input port run: Svc.Sched

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller

    @ The IMU answered with its identity
    event ImuDetected(whoAmI: U8) \
      severity activity high format "IMU detected, WHO_AM_I {x}"

    @ An I2C transaction with the IMU failed
    event ImuReadError(status: Drv.I2cStatus) \
      severity warning high format "IMU read failed: {}"

    @ Communication with the IMU recovered
    event ImuRecovered(errors: U32) \
      severity activity high format "IMU communication recovered after {} failed reads"

    telemetry AccelX: I16
    telemetry AccelY: I16
    telemetry AccelZ: I16
    telemetry ReadErrors: U32

  }

}
