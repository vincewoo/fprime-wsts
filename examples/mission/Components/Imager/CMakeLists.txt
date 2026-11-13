# The camera is driven through its vendor's SDK, which exists for the flight board only:
# SDK_ROOT is set by that board's toolchain, and the codec is in its sysroot. A unit-test
# build (BUILD_TESTING) takes a stub of the camera in its place.
if (BUILD_TESTING)
    register_fprime_library(
        AUTOCODER_INPUTS
            "${CMAKE_CURRENT_LIST_DIR}/Imager.fpp"
        SOURCES
            "${CMAKE_CURRENT_LIST_DIR}/Imager.cpp"
            "${CMAKE_CURRENT_LIST_DIR}/CameraStub.cpp"
    )
    register_fprime_ut(
        SOURCES
            "${CMAKE_CURRENT_LIST_DIR}/test/ut/CameraStubTest.cpp"
        DEPENDS
            Components_Imager
    )
else ()
    find_package(CamCodec REQUIRED)
    register_fprime_library(
        AUTOCODER_INPUTS
            "${CMAKE_CURRENT_LIST_DIR}/Imager.fpp"
        SOURCES
            "${CMAKE_CURRENT_LIST_DIR}/Imager.cpp"
            "${CMAKE_CURRENT_LIST_DIR}/CameraSdk.cpp"
        DEPENDS
            ${SDK_ROOT}/3rdparty/lib/libcamsdk.a
            -lcamcodec
    )
endif ()
