####
# library.cmake: the Mission simulation project, built as an F Prime library
#
# The generated simulated deployment of each element lists this directory under
# `library_locations`, so F Prime includes this file and these modules are named relative
# to it (`SensorSim_SerialSensorSim`, `Models`, `Surrogates_SimLinuxGpio`).
#
# The two elements use different simulation modules. They are built opt-in:
# MISSION_SIM_MODULES lists the directories to build, relative to this file. The variable
# has to be set before this file is read, which for a generated simulated deployment is
# the job of `cmake.cache` in each element's manifest.
####
set(MISSION_SIM_MODULES "" CACHE STRING "Modules of this simulation project to build (directories relative to library.cmake)")

foreach (MISSION_SIM_MODULE IN LISTS MISSION_SIM_MODULES)
    add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/${MISSION_SIM_MODULE}")
endforeach ()

# Configuration overrides for the simulated build of both elements
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config")
