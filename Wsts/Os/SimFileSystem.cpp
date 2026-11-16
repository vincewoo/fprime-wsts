// ======================================================================
// \title  SimFileSystem.cpp
// ======================================================================
#include <Wsts/Os/SimFileSystem.hpp>

#include <cstring>

#include <Fw/Types/Assert.hpp>
#include <Wsts/Os/SimPath.hpp>

namespace Wsts {

namespace {
//! \brief the status of a mapped path, in the terms of Os::FileSystem
//!
//! Its status has no outside-the-sandbox: a path flight software may not use is a path
//! it may not name at all
Os::FileSystemInterface::Status fileSystemStatus(Os::FilePathUtils::Status status) {
    switch (status) {
        case Os::FilePathUtils::VALID:
            return Os::FileSystemInterface::OP_OK;
        case Os::FilePathUtils::TOO_LONG:
            return Os::FileSystemInterface::BUFFER_TOO_SMALL;
        default:
            return Os::FileSystemInterface::INVALID_PATH;
    }
}
}  // namespace

Os::FileSystemInterface::Status SimFileSystem::_removeDirectory(const char* path) {
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(path, mapped);
    if (mappedStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(mappedStatus);
    }
    return Os::Posix::FileSystem::PosixFileSystem::_removeDirectory(mapped.c_str());
}

Os::FileSystemInterface::Status SimFileSystem::_removeFile(const char* path) {
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(path, mapped);
    if (mappedStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(mappedStatus);
    }
    return Os::Posix::FileSystem::PosixFileSystem::_removeFile(mapped.c_str());
}

Os::FileSystemInterface::Status SimFileSystem::_rename(const char* sourcePath, const char* destPath) {
    std::string mappedSource;
    const Os::FilePathUtils::Status sourceStatus = SimPath::map(sourcePath, mappedSource);
    if (sourceStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(sourceStatus);
    }
    std::string mappedDest;
    const Os::FilePathUtils::Status destStatus = SimPath::map(destPath, mappedDest);
    if (destStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(destStatus);
    }
    return Os::Posix::FileSystem::PosixFileSystem::_rename(mappedSource.c_str(), mappedDest.c_str());
}

Os::FileSystemInterface::Status SimFileSystem::_getFreeSpace(const char* path, FwSizeType& totalBytes,
                                                             FwSizeType& freeBytes) {
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(path, mapped);
    if (mappedStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(mappedStatus);
    }
    return Os::Posix::FileSystem::PosixFileSystem::_getFreeSpace(mapped.c_str(), totalBytes, freeBytes);
}

Os::FileSystemInterface::Status SimFileSystem::_getPathType(const char* path, PathType& pathType) {
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(path, mapped);
    if (mappedStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(mappedStatus);
    }
    return Os::Posix::FileSystem::PosixFileSystem::_getPathType(mapped.c_str(), pathType);
}

Os::FileSystemInterface::Status SimFileSystem::_getWorkingDirectory(char* path, FwSizeType bufferSize) {
    FW_ASSERT(path != nullptr);
    const std::string workingDirectory = SimPath::workingDirectory();
    if (bufferSize < workingDirectory.size() + 1) {
        return BUFFER_TOO_SMALL;
    }
    (void)std::memcpy(path, workingDirectory.c_str(), workingDirectory.size() + 1);
    return OP_OK;
}

Os::FileSystemInterface::Status SimFileSystem::_changeWorkingDirectory(const char* path) {
    std::string canonical;
    const Os::FilePathUtils::Status resolve = SimPath::resolve(path, canonical);
    if (resolve != Os::FilePathUtils::VALID) {
        return fileSystemStatus(resolve);
    }
    // The canonical path is absolute, so this puts the sandbox in front of it
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(canonical.c_str(), mapped);
    if (mappedStatus != Os::FilePathUtils::VALID) {
        return fileSystemStatus(mappedStatus);
    }
    // Like the POSIX implementation, a working directory that is not there is an error,
    // not a directory that will be made by the next write
    PathType pathType = PathType::NOT_EXIST;
    const Status typeStatus = Os::Posix::FileSystem::PosixFileSystem::_getPathType(mapped.c_str(), pathType);
    if (typeStatus != OP_OK) {
        return typeStatus;
    }
    if (pathType != PathType::DIRECTORY) {
        return NOT_DIR;
    }
    SimPath::setWorkingDirectory(canonical);
    return OP_OK;
}

}  // namespace Wsts
