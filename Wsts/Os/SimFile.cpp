// ======================================================================
// \title  SimFile.cpp
// ======================================================================
#include <Wsts/Os/SimFile.hpp>

#include <Wsts/Os/SimPath.hpp>

namespace Wsts {

Os::FileInterface::Status SimFile::open(const char* path, Mode mode, OverwriteType overwrite) {
    std::string mapped;
    const Os::FilePathUtils::Status mappedStatus = SimPath::map(path, mapped);
    if (mappedStatus == Os::FilePathUtils::OUTSIDE_SANDBOX) {
        return OUTSIDE_SANDBOX;
    } else if (mappedStatus != Os::FilePathUtils::VALID) {
        // INVALID_PATH or TOO_LONG: the path of flight software cannot name a file
        return INVALID_ARGUMENT;
    }
    if (mode != OPEN_NO_MODE && mode != OPEN_READ && !SimPath::makeDirectories(mapped, false)) {
        return NO_PERMISSION;
    }
    return Os::Posix::File::PosixFile::open(mapped.c_str(), mode, overwrite);
}

}  // namespace Wsts
