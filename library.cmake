####
# library.cmake: F Prime library entry point for the SSE (software simulation environment) toolkit
#
# Add this repository to a project's `library_locations` (settings.ini) to make the Sse.*
# surrogate components available. Generated simulation deployments do this automatically.
####
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Core")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Drv/SimByteStream")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Drv/SimI2c")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Drv/SimSpi")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Drv/SimGpio")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Svc/SimTick")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Svc/SimControl")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sse/Svc/SimSetup")
