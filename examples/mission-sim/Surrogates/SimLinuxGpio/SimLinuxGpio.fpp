# Simulation surrogate mirroring Drv.LinuxGpioDriver. Created by `fprime-wsts scaffold`.

module Sim {

  @ Simulation surrogate for Drv.LinuxGpioDriver: same ports and dictionary entries, so a
  @ simulated deployment keeps the flight dictionary for its GPIO pins
  passive component SimLinuxGpio {

    import Drv.Gpio

    # ----------------------------------------------------------------------
    # Special ports
    # ----------------------------------------------------------------------

    event port Log

    text event port LogText

    time get port Time

    # ----------------------------------------------------------------------
    # Events
    # ----------------------------------------------------------------------
    event OpenChip(chip: string, chipLabel: string, pin: U32, pinMessage: string) severity diagnostic format "Opened GPIO chip {}[{}] pin {}[{}]"

    event OpenChipError(chip: string, status: Os.FileStatus) severity warning high format "Failed to open GPIO chip {}: {}"

    event OpenPinError(chip: string, pin: U32, pinMessage: string, status: Os.FileStatus) severity warning high format "Failed to open GPIO chip {} pin {} [{}]: {}"

    event InterruptReadError(expected: U32, got: U32) severity warning high format "Interrupt data read expected {} byes and got {}"

    event PollingError(error_number: I32) severity warning high format "Interrupt polling returned errno: {}"

    event InterruptTimeError(status: Os.RawTimeStatus) severity warning high format "Failed to read interrupt timestamp: {}"

  }

}
