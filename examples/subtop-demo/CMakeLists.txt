####
# WSTS toolkit example project: SubtopDemo, a VxWorks deployment built from subtopologies
####
cmake_minimum_required(VERSION 3.26)
project(SubtopDemoProject VERSION 1.0.0 LANGUAGES C CXX)

find_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/../../lib/fprime/cmake")

add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config/project")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/SubtopDemo")
