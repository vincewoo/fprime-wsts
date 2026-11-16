// ======================================================================
// \title  SimDirectory.cpp
// ======================================================================
#include <Wsts/Os/SimDirectory.hpp>

#include <Wsts/Os/SimPath.hpp>

namespace Wsts {

Os::DirectoryInterface::Status SimDirectory::open(const char* path, OpenMode mode) {
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(path, mapped);
    if (mappedStatus != Os::FilePathUtils::VALID) {
        // The directory status has neither outside-the-sandbox nor a too-long path
        return OTHER_ERROR;
    }
    if (mode != OpenMode::READ && !SimPath::makeDirectories(mapped, mode == OpenMode::CREATE_IF_MISSING)) {
        return NO_PERMISSION;
    }
    return Os::Posix::Directory::PosixDirectory::open(mapped.c_str(), mode);
}

}  // namespace Wsts
