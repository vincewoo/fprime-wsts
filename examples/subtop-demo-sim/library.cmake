####
# library.cmake: the SubtopDemo simulation project, built as an F Prime library
#
# Simulator components are built opt-in: SENSOR_SIM_COMPONENTS lists the directories under
# SensorSim/ to build. The variable has to be set before this file is read, which for a
# generated simulated deployment is the job of `cmake.cache` in the manifest.
####
set(SENSOR_SIM_COMPONENTS "" CACHE STRING "Simulator modules of this library to build (directories under SensorSim/)")

foreach (SENSOR_SIM_COMPONENT IN LISTS SENSOR_SIM_COMPONENTS)
    add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/SensorSim/${SENSOR_SIM_COMPONENT}")
endforeach ()
