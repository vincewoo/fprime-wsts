# Project configuration: files here replace the same-named configuration files of the
# framework and of the libraries.

# Constants the overrides below are written with
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/dep_config")

register_fprime_config(
    CONFIGURATION_OVERRIDES
        "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/CdhCoreConfig.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/SensorLinkConfig.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp"
        # Supplied by the platform library fprime-vxworks, whatever the platform
        "${CMAKE_CURRENT_LIST_DIR}/VxWorksCfg.hpp"
    INTERFACE
)
