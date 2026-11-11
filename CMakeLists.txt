####
# Stand-alone build of the WSTS toolkit, used to build and unit test it on its own.
# Projects do not use this file: they list the repository in `library_locations` and
# F Prime includes library.cmake.
####
cmake_minimum_required(VERSION 3.26)
project(FprimeWsts VERSION 0.1.0 LANGUAGES C CXX)

find_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/lib/fprime/cmake")

include("${CMAKE_CURRENT_LIST_DIR}/library.cmake")
