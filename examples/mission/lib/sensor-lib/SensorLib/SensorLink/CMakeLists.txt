add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/SensorLinkConfig/")

register_fprime_module(
    EXCLUDE_FROM_ALL
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/SensorLink.fpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/SubtopologyTopologyDefs.hpp"
    INTERFACE
    DEPENDS
        SensorLib_SensorLink_SensorLinkConfig
        SensorLib_SerialSensor
        VxWorks_Drv_VxWorksUartDriver
        Svc_BufferManager
)
