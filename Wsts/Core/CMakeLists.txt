####
# Wsts/Core: simulation backplane, control core, bridge and reference models.
#
# Plain C++ (no F Prime dependencies) so that it can be reasoned about and unit tested on
# its own. Simulation-only code: it uses the C++ standard library freely.
####
find_package(Threads REQUIRED)

register_fprime_module(
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Json.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Bus.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Control.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Clock.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Bridge.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Models.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Link.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/Runtime.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/Json.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Bus.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Control.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Clock.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Bridge.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Models.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Link.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/Runtime.hpp"
    DEPENDS
        Threads::Threads
)

# The module has no F Prime dependencies to inherit include paths from
target_include_directories(Wsts_Core PUBLIC "${CMAKE_CURRENT_LIST_DIR}/../..")

register_fprime_ut(
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/CoreTestMain.cpp"
    DEPENDS
        Wsts_Core
)
