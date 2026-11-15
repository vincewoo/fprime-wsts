####
# Wsts/VxWorks: the VxWorks API shim (docs/vxworks-shim.md)
#
# A POSIX-backed subset of taskLib, semLib, msgQLib, wdLib, sysLib, tickLib, intLib, logLib,
# errnoLib and ioLib, so that project code which includes VxWorks headers compiles and runs
# unchanged in a simulated deployment. A deployment turns it on with `shims: [vxworks]` in
# its manifest: the overlay then puts include/ on every module's include path (after the
# module's own directories) and links this module into the deployment.
####
register_fprime_module(
        Wsts_VxWorks
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Kernel.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Objects.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/Kernel.hpp"
    DEPENDS
        Fw_Types
        Os
        Wsts_Core
)
target_include_directories(Wsts_VxWorks PUBLIC "${CMAKE_CURRENT_LIST_DIR}/include")

# Real time and stepped time, with the task of a simulated deployment
register_fprime_ut(
        Wsts_VxWorks_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/VxWorksTests.cpp"
    DEPENDS
        Fw_Types
        Os
        Wsts_Core
        Wsts_VxWorks
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_Task
)
