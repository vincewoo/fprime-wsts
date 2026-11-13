# Unit conversions: plain C++ with an include directory of its own, which the target
# exports. Unit tests of the components get fixed conversions from a stub, which exports
# nothing: those tests include the header by its path.
if (BUILD_TESTING)
    register_fprime_module(
        SOURCES
            "${CMAKE_CURRENT_LIST_DIR}/stubs/convert_stub.cpp"
    )
else ()
    register_fprime_module(
        SOURCES
            "${CMAKE_CURRENT_LIST_DIR}/convert.cpp"
    )
    target_include_directories(${FPRIME_CURRENT_MODULE} PUBLIC "${CMAKE_CURRENT_LIST_DIR}/include")
endif ()
