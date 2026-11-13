####
# What the Payload board's toolchain provides and a workstation lacks (`cmake.include_early`
# in payload.yaml). Included in the generated project before the framework and the
# libraries are read, so every module sees it.
#
# Checked in, so that the build is the same for everyone: CPATH and LIBRARY_PATH would do
# the same for one shell only.
####

# <board/revision.h>, which the flight toolchain's sysroot has
include_directories("${CMAKE_CURRENT_LIST_DIR}/host/include")

# A directory with host builds of libraries would be named the same way:
#   link_directories("${CMAKE_CURRENT_LIST_DIR}/host/lib")
