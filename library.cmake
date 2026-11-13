####
# library.cmake: F Prime library entry point for the WSTS (Workstation Test Set) toolkit
#
# Add this repository to a project's `library_locations` (settings.ini) to make the Wsts.*
# surrogate components available. Generated simulation deployments do this automatically.
####
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Core")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Drv/SimByteStream")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Drv/BusByteStreamDevice")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Drv/SimI2c")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Drv/SimSpi")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Drv/SimGpio")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Drv/SimRegister")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Svc/SimTick")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Svc/SimCycle")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Svc/SimControl")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Wsts/Svc/SimSetup")
