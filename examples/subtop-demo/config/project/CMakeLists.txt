# Project configuration: files here replace the same-named configuration files of the
# framework and of the libraries.
register_fprime_config(
    CONFIGURATION_OVERRIDES
        "${CMAKE_CURRENT_LIST_DIR}/CdhCoreConfig.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/SensorLinkConfig.fpp"
    INTERFACE
)
