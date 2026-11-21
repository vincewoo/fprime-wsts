#!/usr/bin/env bash
# The flight project and its simulation project, to which someone has added a barometer model
# skeleton (fprime-wsts new-model) at an I2C address flight software never talks to
set -eu
fixture="$(cd "$(dirname "$0")/../../../wsts-configure/evals/_fixture" && pwd)"
"$fixture/sibling-flight.sh" orbiter-fsw
"$fixture/sibling-sim.sh" orbiter-fsw
cd ../orbiter-fsw-sim
fprime-wsts new-model Barometer --kind i2c --endpoint i2cBus --address 0x76 --manifest payload.yaml > /dev/null
sed -i 's#"${CMAKE_CURRENT_LIST_DIR}/ImuModel.cpp"#"${CMAKE_CURRENT_LIST_DIR}/ImuModel.cpp"\n        "${CMAKE_CURRENT_LIST_DIR}/Barometer.cpp"#; s#"${CMAKE_CURRENT_LIST_DIR}/ImuModel.hpp"#"${CMAKE_CURRENT_LIST_DIR}/ImuModel.hpp"\n        "${CMAKE_CURRENT_LIST_DIR}/Barometer.hpp"#' Models/CMakeLists.txt
fprime-wsts generate wsts-system.yaml > /dev/null
git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -qm "Start a barometer model"
