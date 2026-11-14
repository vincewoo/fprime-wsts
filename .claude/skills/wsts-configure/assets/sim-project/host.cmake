####
# host.cmake: what the flight toolchain's sysroot provides and a workstation lacks.
# Named under `cmake.include_early` in the manifest; included before the framework and the
# libraries are read, so every module sees it. Checked in, so everyone builds the same way
# (CPATH and LIBRARY_PATH would only fix one shell).
####
include_directories("${CMAKE_CURRENT_LIST_DIR}/host/include")
# link_directories("${CMAKE_CURRENT_LIST_DIR}/host/lib")
