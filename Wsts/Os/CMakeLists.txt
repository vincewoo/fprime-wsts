####
# Wsts/Os: implementations of F Prime's operating system interfaces for simulated deployments
#
# Wsts_Os_Queue is F Prime's generic priority queue with a count of the messages that are
# outstanding (Wsts/Core/Activity). A simulated deployment chooses it with
# CHOOSES_IMPLEMENTATIONS, which the generated overlay does.
####
register_fprime_module(
        Wsts_Os_CountedQueue
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/CountedQueue.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/CountedQueue.hpp"
    DEPENDS
        Fw_Types
        Os_Generic_PriorityQueue_Implementation
        Wsts_Core
)
register_fprime_implementation(
        Wsts_Os_Queue
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/DefaultCountedQueue.cpp"
    IMPLEMENTS
        Os_Queue
    DEPENDS
        Fw_Types
        Wsts_Os_CountedQueue
)

# The framework's own tests of an Os::Queue, and the counting, against this implementation
register_fprime_ut(
        Wsts_Os_CountedQueue_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/CountedQueueTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/queue/CommonTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/queue/QueueRules.cpp"
    DEPENDS
        Fw_Types
        Fw_Time
        Os
        STest
        Wsts_Core
        Wsts_Os_CountedQueue
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_Queue
)
if (TARGET Wsts_Os_CountedQueue_ut)
    target_compile_options(Wsts_Os_CountedQueue_ut PRIVATE -Wno-conversion)
    target_include_directories(Wsts_Os_CountedQueue_ut PRIVATE "${FPRIME_FRAMEWORK_PATH}/Os/Generic/test/ut")
endif()
