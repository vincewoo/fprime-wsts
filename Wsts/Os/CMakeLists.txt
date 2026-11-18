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
        Drv_Ip
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

# Wsts_Os_File is the POSIX file system of the framework, with every path of flight
# software mapped into the sandbox of the run (SimPath). One implementation covers
# Os::File, Os::FileSystem and Os::Directory, as the framework's own DefaultFile.cpp does
register_fprime_module(
        Wsts_Os_SimFile
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/SimPath.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimFile.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimFileSystem.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimDirectory.cpp"
    HEADERS
        "${CMAKE_CURRENT_LIST_DIR}/FilePathUtilsCompat.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimPath.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimFile.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimFileSystem.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/SimDirectory.hpp"
    DEPENDS
        Fw_Types
        Os
        Os_File_Posix_Implementation
)
register_fprime_implementation(
        Wsts_Os_File
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/DefaultSimFile.cpp"
    IMPLEMENTS
        Os_File
    DEPENDS
        Fw_Types
        Wsts_Os_SimFile
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

# The framework's own tests of an Os::File (with its POSIX tests, which bring main()), against this
# implementation. The suite names files of the host directly: SuiteSandboxRoot.cpp pins the sandbox
# to the root, where the mapping is one-to-one
register_fprime_ut(
        Wsts_Os_SimFile_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/SuiteSandboxRoot.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/file/CommonTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/file/FileRules.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/Posix/test/ut/PosixFileTests.cpp"
    DEPENDS
        Fw_Types
        Os_Test_File_SyntheticFileSystem
        STest
        Utils
        Wsts_Os_SimFile
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_File
)
if (TARGET Wsts_Os_SimFile_ut)
    target_compile_options(Wsts_Os_SimFile_ut PRIVATE -Wno-conversion)
endif()

# The framework's own tests of an Os::Directory, in a sandbox at the working directory, where its
# files relative to it are the same place to the calls of the host it makes them with. The
# framework's tests of an Os::FileSystem are not run against this implementation: their rule of
# the working directory expects the strings of the host, which a sandbox at the root cannot give
register_fprime_ut(
        Wsts_Os_SimDirectory_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/SuiteSandboxCwd.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/directory/CommonTests.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/test/ut/directory/DirectoryRules.cpp"
        "${FPRIME_FRAMEWORK_PATH}/Os/Posix/test/ut/PosixDirectoryTests.cpp"
    DEPENDS
        Fw_Types
        STest
        Wsts_Os_SimFile
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_File
)
if (TARGET Wsts_Os_SimDirectory_ut)
    target_compile_options(Wsts_Os_SimDirectory_ut PRIVATE -Wno-conversion)
endif()

# The sandbox itself: where the paths of flight software land, and what is refused
register_fprime_ut(
        Wsts_Os_SimFileTests_ut
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/test/ut/SimFileTests.cpp"
    DEPENDS
        Fw_Types
        STest
        Wsts_Os_SimFile
    CHOOSES_IMPLEMENTATIONS
        Wsts_Os_File
)
