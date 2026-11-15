# The heartbeat of the command and data handling computer. Written against the VxWorks
# kernel API (taskLib, semLib, msgQLib, wdLib): it builds for the VxWorks target only.
register_fprime_module(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/Heartbeat.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Heartbeat.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/Heartbeat.hpp"
)
