####
# WSTS toolkit example project: VxDemo, a VxWorks deployment
####
cmake_minimum_required(VERSION 3.26)
project(VxDemoProject VERSION 1.0.0 LANGUAGES C CXX)

find_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/../../lib/fprime/cmake")

add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Components")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/VxDemo")
