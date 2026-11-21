#!/usr/bin/env bash
# The flight project and its simulation project, in step; then a flight commit the simulation
# has not seen: the FPGA's register block is renamed (which the manifest names) and a status
# LED on a new GPIO pin is added
set -eu
fixture="$(cd "$(dirname "$0")/../../../wsts-configure/evals/_fixture" && pwd)"
"$fixture/sibling-flight.sh" orbiter-fsw
"$fixture/sibling-sim.sh" orbiter-fsw
sleep 2  # the flight commit comes after the generation, as it would
top=../orbiter-fsw/Payload/Top
sed -i 's/fpgaRegs/fpgaBlock/g' "$top/instances.fpp" "$top/topology.fpp" "$top/PayloadTopology.cpp"
sed -i 's/^  instance spiBus:/  instance ledPin: Drv.LinuxGpioDriver base id 0x20038000\n\n  instance spiBus:/' "$top/instances.fpp"
sed -i 's/^    instance switchPin$/    instance switchPin\n    instance ledPin/' "$top/topology.fpp"
git -C ../orbiter-fsw -c user.name=fixture -c user.email=fixture@example.invalid commit -qam \
  "Payload: name the FPGA register block after the board ICD (fpgaBlock); add the status LED pin"
