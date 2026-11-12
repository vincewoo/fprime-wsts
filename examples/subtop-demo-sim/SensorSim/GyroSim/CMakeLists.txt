register_fprime_library(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/GyroSim.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/GyroSim.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/GyroSim.hpp"
    DEPENDS
        # A module of the vendored library, by the name it has there
        SimCommon_Types
        # A flight module, by the name the simulation's sim.cmake gives it
        Flight_SerialSensor
)
