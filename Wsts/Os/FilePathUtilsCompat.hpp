// ======================================================================
// \title  FilePathUtilsCompat.hpp
// \brief  Os::FilePathUtils, for frameworks that do not have it
//
// F Prime 4.4 has Os/FilePathUtils.hpp. Before that the sandbox of SimPath has nothing to
// lean on, so the same names are declared here, with the same behaviour (the framework's
// own implementation, reduced to what Wsts uses: paths are resolved without touching the
// file system, and containment is a prefix test on a '/' boundary).
// ======================================================================
#ifndef WSTS_OS_FILEPATHUTILSCOMPAT_HPP
#define WSTS_OS_FILEPATHUTILSCOMPAT_HPP

#if defined(__has_include) && __has_include(<Os/FilePathUtils.hpp>)
#include <Os/FilePathUtils.hpp>
// Os::File::OUTSIDE_SANDBOX came with it
#define WSTS_HAS_FILE_OUTSIDE_SANDBOX 1
#else
#define WSTS_HAS_FILE_OUTSIDE_SANDBOX 0

#include <Fw/FPrimeBasicTypes.hpp>
#include <Fw/Types/Assert.hpp>
#include <Fw/Types/StringUtils.hpp>
#include <config/FppConstantsAc.hpp>
#include <cstring>

namespace Os {
namespace FilePathUtils {

enum Status {
    VALID,
    OUTSIDE_SANDBOX,
    INVALID_PATH,
    TOO_LONG,
};

static constexpr FwSizeType MAX_PATH_LENGTH = FileNameStringSize;

namespace Detail {

inline Status build(const char* path, const char* baseDir, char* out, FwSizeType outSize) {
    const FwSizeType pathLen = Fw::StringUtils::string_length(path, MAX_PATH_LENGTH);
    if (pathLen >= MAX_PATH_LENGTH) {
        return INVALID_PATH;
    }
    if (path[0] == '/') {
        if (pathLen + 1 > outSize) {
            return TOO_LONG;
        }
        (void)std::memcpy(out, path, pathLen + 1);
        return VALID;
    }
    if (baseDir == nullptr) {
        return INVALID_PATH;
    }
    const FwSizeType baseLen = Fw::StringUtils::string_length(baseDir, MAX_PATH_LENGTH);
    if (baseLen >= MAX_PATH_LENGTH || baseLen == 0 || baseDir[0] != '/') {
        return INVALID_PATH;
    }
    const FwSizeType slash = (baseDir[baseLen - 1] != '/') ? 1 : 0;
    if (baseLen + slash + pathLen + 1 > outSize) {
        return TOO_LONG;
    }
    (void)std::memcpy(out, baseDir, baseLen);
    FwSizeType pos = baseLen;
    if (slash != 0) {
        out[pos++] = '/';
    }
    (void)std::memcpy(&out[pos], path, pathLen);
    out[pos + pathLen] = '\0';
    return VALID;
}

//! Collapse "." and ".." and repeated separators of an absolute path, in place
inline Status collapse(char* path) {
    const FwSizeType length = Fw::StringUtils::string_length(path, MAX_PATH_LENGTH);
    if (length >= MAX_PATH_LENGTH) {
        return INVALID_PATH;
    }
    FwSizeType write = 1;
    for (FwSizeType read = 1; read <= length;) {
        const FwSizeType start = read;
        for (; read < length && path[read] != '/'; read++) {
        }
        const FwSizeType segment = read - start;
        read = (read < length) ? read + 1 : length + 1;
        if (segment == 0 || (segment == 1 && path[start] == '.')) {
            continue;
        }
        if (segment == 2 && path[start] == '.' && path[start + 1] == '.') {
            if (write > 1) {
                write--;
                for (; write > 1 && path[write - 1] != '/'; write--) {
                }
            }
            continue;
        }
        (void)std::memmove(&path[write], &path[start], segment);
        write += segment;
        path[write++] = '/';
    }
    if (write > 1) {
        write--;
    }
    path[write] = '\0';
    return VALID;
}

}  // namespace Detail

inline Status resolvePath(const char* path, const char* baseDir, char* resolvedOut, FwSizeType resolvedSize) {
    FW_ASSERT(resolvedOut != nullptr);
    if (path == nullptr || path[0] == '\0') {
        return INVALID_PATH;
    }
    const Status built = Detail::build(path, baseDir, resolvedOut, resolvedSize);
    return (built != VALID) ? built : Detail::collapse(resolvedOut);
}

inline Status checkContainment(const char* resolvedPath, const char* allowedDirectory) {
    FW_ASSERT(resolvedPath != nullptr);
    FW_ASSERT(allowedDirectory != nullptr);
    const FwSizeType allowedLen = Fw::StringUtils::string_length(allowedDirectory, MAX_PATH_LENGTH);
    const FwSizeType pathLen = Fw::StringUtils::string_length(resolvedPath, MAX_PATH_LENGTH);
    if (allowedLen >= MAX_PATH_LENGTH || pathLen >= MAX_PATH_LENGTH) {
        return INVALID_PATH;
    }
    if (allowedLen == 0 || pathLen == 0 || allowedDirectory[allowedLen - 1] != '/') {
        return OUTSIDE_SANDBOX;
    }
    if (pathLen == allowedLen - 1 && std::memcmp(resolvedPath, allowedDirectory, pathLen) == 0) {
        return VALID;
    }
    if (pathLen < allowedLen || std::memcmp(resolvedPath, allowedDirectory, allowedLen) != 0) {
        return OUTSIDE_SANDBOX;
    }
    return VALID;
}

}  // namespace FilePathUtils
}  // namespace Os

#endif  // __has_include(<Os/FilePathUtils.hpp>)

#endif
