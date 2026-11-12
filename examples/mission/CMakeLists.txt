####
# WSTS toolkit example project: Mission, the flight software of a spacecraft with two
# computers joined by an F Prime hub.
#
# An ordinary F Prime project that knows nothing about simulation. Its simulation lives in
# a project of its own, examples/mission-sim, which only reads this one.
####
cmake_minimum_required(VERSION 3.26)
project(MissionProject VERSION 1.0.0 LANGUAGES C CXX)

# The board the sensors of lib/sensor-lib are wired for. That library's library.cmake stops
# without it, so it is set before F Prime reads the libraries.
set(SENSOR_LIB_BOARD cdh-rev-b)

# Which vehicle this build is for. Both computers report it when they start.
add_compile_definitions(MISSION_VEHICLE_ID=2)

find_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/../../lib/fprime/cmake")

add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config/project")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Components")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Cdh")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Payload")
