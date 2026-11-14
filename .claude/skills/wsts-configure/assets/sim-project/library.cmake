####
# library.cmake: this simulation project, built as an F Prime library
#
# Only needed once the project has modules of its own (surrogates, device models,
# simulator components, configuration overrides). Each generated deployment lists this
# directory under `library_locations`; F Prime includes this file, and the modules below
# are named relative to it (Models, Surrogates_SimFoo).
#
# For a system whose elements use different modules, build them opt-in: list the
# directories in a cache variable, which each element's manifest sets with `cmake.cache`
# (examples/mission-sim/library.cmake does this with MISSION_SIM_MODULES).
####
# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Models")
# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Surrogates/SimFoo")

# Configuration overrides for the simulated build go in a directory of their own,
# with register_fprime_config(CONFIGURATION_OVERRIDES ...) in its CMakeLists.txt:
# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config")
