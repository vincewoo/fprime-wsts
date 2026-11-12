# Configuration the simulated build needs beyond flight's: files here replace the
# same-named files of the framework, the libraries and the flight project.
# fprime-wsts registers this module after the flight project's configuration, so it wins.
register_fprime_config(
    CONFIGURATION_OVERRIDES
        "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp"
    INTERFACE
)
