####
# WSTS toolkit example project: Mission, the flight software of a spacecraft with two
# computers joined by an F Prime hub.
#
# An ordinary F Prime project that knows nothing about simulation. Its simulation lives in
# a project of its own, examples/mission-sim, which only reads this one.
####
cmake_minimum_required(VERSION 3.26)
project(MissionProject VERSION 1.0.0 LANGUAGES C CXX)

find_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/../../lib/fprime/cmake")

add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config/project")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Components")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Cdh")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Payload")
