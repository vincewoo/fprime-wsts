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

# Wsts_Os_RawTime is the POSIX raw time, giving simulated time in stepped time
register_fprime_module(
        Wsts_Os_SimRawTime
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/SimRawTime.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/SimRawTime.hpp"
    DEPENDS
        Fw_Types
        Os_RawTime_Posix_Implementation
        Wsts_Core
)
register_fprime_implementation(
        Wsts_Os_RawTime
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/DefaultSimRawTime.cpp"
    IMPLEMENTS
        Os_RawTime
    DEPENDS
        Fw_Types
        Wsts_Os_SimRawTime
)

# Wsts_Os_Task is the POSIX task, taking its turn in stepped time before its routine runs
register_fprime_module(
        Wsts_Os_SimTask
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/SimTask.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/SimTask.hpp"
    DEPENDS
        Fw_Types
        Os_Task_Posix_Implementation
        Wsts_Core
)
register_fprime_implementation(
        Wsts_Os_Task
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/DefaultSimTask.cpp"
    IMPLEMENTS
        Os_Task
    DEPENDS
        Fw_Types
        Wsts_Os_SimTask
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

# The framework's own tests of an Os::Task (with its POSIX tests, which bring main()), and the turn
register_fprime_ut(
        Wsts_Os_SimTaskTurn_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/SimTaskTests.cpp"
    DEPENDS
        Fw_Types
        Wsts_Core
        Wsts_Os_SimTask
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_Task
)
register_fprime_ut(
        Wsts_Os_SimTask_ut
    SOURCES
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/task/CommonTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/task/TaskRules.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/Posix/test/ut/PosixTaskTests.cpp"
    DEPENDS
        Fw_Types
        STest
        Wsts_Core
        Wsts_Os_SimTask
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_Task
)
if (TARGET Wsts_Os_SimTask_ut)
    target_compile_options(Wsts_Os_SimTask_ut PRIVATE -Wno-conversion)
endif()

# The framework's own tests of an Os::RawTime (with its POSIX tests, which bring main()), and simulated time
register_fprime_ut(
        Wsts_Os_SimRawTime_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/SimRawTimeTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/rawtime/CommonTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/rawtime/RawTimeRules.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/Posix/test/ut/PosixRawTimeTests.cpp"
    DEPENDS
        Fw_Types
        Fw_Time
        STest
        Wsts_Core
        Wsts_Os_SimRawTime
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_RawTime
)
if (TARGET Wsts_Os_SimRawTime_ut)
    target_compile_options(Wsts_Os_SimRawTime_ut PRIVATE -Wno-conversion)
endif()
