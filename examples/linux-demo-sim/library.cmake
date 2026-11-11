####
# library.cmake: the LinuxDemo simulation project, built as an F Prime library
#
# The generated simulated deployment lists this directory under `library_locations`, so
# F Prime includes this file and these modules are named relative to it (`Models`,
# `Surrogates_SimLinuxGpio`).
####
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Surrogates/SimLinuxGpio")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Models")
