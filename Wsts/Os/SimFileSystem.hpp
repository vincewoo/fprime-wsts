// ======================================================================
// \title  SimFileSystem.hpp
// \brief  The Os::FileSystem of a simulated deployment: every path inside the sandbox
//
// Chosen by a simulated deployment in place of Os_File_Posix (CHOOSES_IMPLEMENTATIONS
// Wsts_Os_File, which carries the file system with it). Every path of every operation is
// mapped into the sandbox of the run (SimPath.hpp) first, so that the remove, rename and
// directory operations of flight software stay inside it. The working directory is flight
// software's own: what getWorkingDirectory reports is a path as flight software sees it,
// starting at the root of the sandbox, and relative paths resolve against it.
// ======================================================================
#ifndef WSTS_OS_SIMFILESYSTEM_HPP
#define WSTS_OS_SIMFILESYSTEM_HPP

#include <Os/Posix/FileSystem.hpp>

namespace Wsts {

class SimFileSystem : public Os::Posix::FileSystem::PosixFileSystem {
  public:
    SimFileSystem() = default;
    ~SimFileSystem() override = default;

    Status _removeDirectory(const char* path) override;

    Status _removeFile(const char* path) override;

    Status _rename(const char* sourcePath, const char* destPath) override;

    Status _getFreeSpace(const char* path, FwSizeType& totalBytes, FwSizeType& freeBytes) override;

    Status _getPathType(const char* path, PathType& pathType) override;

    Status _getWorkingDirectory(char* path, FwSizeType bufferSize) override;

    Status _changeWorkingDirectory(const char* path) override;
};

}  // namespace Wsts

#endif
