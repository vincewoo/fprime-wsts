# Device models: plain C++ on the WSTS bus, no F Prime model
register_fprime_library(
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/ImuModel.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/ImuModel.hpp"
    DEPENDS
        Wsts_Core
)
