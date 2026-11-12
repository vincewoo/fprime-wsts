# sensor-lib: a project library with a device manager and the subtopology that wires it to
# its serial port
#
# The project says which board the sensors are wired for, before F Prime is read:
#
#   set(SENSOR_LIB_BOARD cdh-rev-b)
if (NOT DEFINED SENSOR_LIB_BOARD)
    message(FATAL_ERROR "sensor-lib: SENSOR_LIB_BOARD is not set. Set it in the project's CMakeLists.txt before F Prime is included.")
endif()
message(STATUS "sensor-lib: board ${SENSOR_LIB_BOARD}")

add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/SensorLib")
