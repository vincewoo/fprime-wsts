#!/usr/bin/env bash
# The flight project, with a heater component that names a serial device it never opens
set -eu
"$(dirname "$0")/../_fixture/sibling-flight.sh" orbiter-fsw
heater=../orbiter-fsw/Components/HeaterControl/HeaterControl.cpp
sed -i '1i // Bench builds of the heater once logged to a debug console on this port\n#include <termios.h>\nextern const char* const HEATER_DEBUG_PORT;\nconst char* const HEATER_DEBUG_PORT = "/dev/ttyACM0";' "$heater"
git -C ../orbiter-fsw -c user.name=fixture -c user.email=fixture@example.invalid commit -qam "heater debug port"
