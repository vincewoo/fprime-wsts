add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Top/")

# A downloadable kernel module: no main(). The entry points in Entry.cpp are called from
# the VxWorks start-up script.
register_fprime_deployment(
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Entry.cpp"
    DEPENDS
        ${FPRIME_CURRENT_MODULE}_Top
)
