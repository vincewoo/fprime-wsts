# FPP before 3.4 passes the enumerations of a command handler by value (SimControl.hpp)
get_expected_tool_version("fprime-fpp" WSTS_FPP_VERSION)
string(REGEX REPLACE "^v" "" WSTS_FPP_VERSION "${WSTS_FPP_VERSION}")
set(WSTS_ENUM_BY_VALUE FALSE)
if (WSTS_FPP_VERSION AND WSTS_FPP_VERSION VERSION_LESS 3.4.0)
    set(WSTS_ENUM_BY_VALUE TRUE)
endif()

register_fprime_library(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/SimControl.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/SimControl.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/SimControl.hpp"
    DEPENDS
        Wsts_Core
)

if (WSTS_ENUM_BY_VALUE)
    target_compile_definitions(${FPRIME_CURRENT_MODULE} PUBLIC WSTS_FPP_ENUM_BY_VALUE)
endif()
