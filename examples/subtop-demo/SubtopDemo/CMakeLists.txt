add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Top/")

# The flight image is a downloadable kernel module started from the VxWorks start-up
# script; Main.cpp also provides main() for a native build.
register_fprime_deployment(
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Main.cpp"
    DEPENDS
        ${FPRIME_CURRENT_MODULE}_Top
)
