// ======================================================================
// \title  SimPath.cpp
// ======================================================================
#include <Wsts/Os/SimPath.hpp>

#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <mutex>

namespace Wsts {

namespace {
// A path as long as flight software may name one: the sandbox in front of it has to fit
// the buffers of the framework's path utilities too
constexpr FwSizeType PATH_BUFFER = Os::FilePathUtils::MAX_PATH_LENGTH;

// The working directory of flight software, guarded: several tasks may change it and open
// files at once, and in real time they run at the same time
std::mutex& cwdMutex() {
    static std::mutex mutex;
    return mutex;
}

std::string& cwdStorage() {
    static std::string cwd = "/";
    return cwd;
}

//! \brief the sandbox without a trailing separator, so that paths and the check of what
//! stays inside it are built the same way whatever the environment said
std::string clean(std::string path) {
    while (path.size() > 1 && path.back() == '/') {
        path.pop_back();
    }
    return path;
}
}  // namespace

std::string SimPath::sandbox() {
    // Read on every use: the run sets it before the deployment starts, and the unit tests
    // change it between cases
    for (const char* name : {"WSTS_FILES_DIR", "WSTS_DIR"}) {
        const char* value = ::getenv(name);
        if (value != nullptr && value[0] != '\0') {
            if (value[0] == '/') {
                return clean(std::string(value));
            }
            // A relative sandbox is against the working directory of the process
            char cwd[PATH_BUFFER];
            if (::getcwd(cwd, sizeof cwd) != nullptr) {
                return clean(std::string(cwd) + "/" + value);
            }
            return clean(std::string(value));
        }
    }
    // Without any of them the working directory is the sandbox. If it cannot be had the
    // root takes over: paths resolve, and their opens fail there instead of on the host
    char cwd[PATH_BUFFER];
    if (::getcwd(cwd, sizeof cwd) != nullptr) {
        return clean(std::string(cwd));
    }
    return std::string("/");
}

Os::FilePathUtils::Status SimPath::resolve(const char* path, std::string& canonical) {
    if (path == nullptr || path[0] == '\0') {
        return Os::FilePathUtils::INVALID_PATH;
    }
    // Relative paths are of flight software, and resolve against its working directory;
    // absolute ones are already in the form flight software sees
    const std::string base = (path[0] == '/') ? std::string("/") : SimPath::workingDirectory();
    char resolved[PATH_BUFFER];
    const Os::FilePathUtils::Status status = Os::FilePathUtils::resolvePath(path, base.c_str(), resolved, PATH_BUFFER);
    if (status != Os::FilePathUtils::VALID) {
        return status;
    }
    canonical = resolved;
    return Os::FilePathUtils::VALID;
}

Os::FilePathUtils::Status SimPath::map(const char* path, std::string& mapped) {
    std::string canonical;
    const Os::FilePathUtils::Status resolve = SimPath::resolve(path, canonical);
    if (resolve != Os::FilePathUtils::VALID) {
        return resolve;
    }
    const std::string sandbox = SimPath::sandbox();
    // Collapsed again: the sandbox in front of it may make the path longer than the one
    // flight software named
    char host[PATH_BUFFER];
    const Os::FilePathUtils::Status joined =
        Os::FilePathUtils::resolvePath((sandbox + canonical).c_str(), "/", host, PATH_BUFFER);
    if (joined != Os::FilePathUtils::VALID) {
        return joined;
    }
    // At the root the sandbox is its own separator
    const std::string allowed = (sandbox == "/") ? sandbox : sandbox + "/";
    if (Os::FilePathUtils::checkContainment(host, allowed.c_str()) != Os::FilePathUtils::VALID) {
        return Os::FilePathUtils::OUTSIDE_SANDBOX;
    }
    mapped = host;
    return Os::FilePathUtils::VALID;
}

std::string SimPath::workingDirectory() {
    const std::lock_guard<std::mutex> lock(cwdMutex());
    return cwdStorage();
}

void SimPath::setWorkingDirectory(const std::string& canonical) {
    const std::lock_guard<std::mutex> lock(cwdMutex());
    cwdStorage() = canonical;
}

bool SimPath::makeDirectories(const std::string& mapped, bool withLast) {
    // Parents before children, and EEXIST for the ones that are there already
    std::string::size_type end = mapped.find('/', 1);
    while (end != std::string::npos || withLast) {
        if (end == std::string::npos) {
            end = mapped.size();
        }
        const std::string directory = mapped.substr(0, end);
        if (::mkdir(directory.c_str(), S_IRWXU | S_IRWXG | S_IRWXO) != 0 && errno != EEXIST) {
            return false;
        }
        if (end == mapped.size()) {
            break;
        }
        end = mapped.find('/', end + 1);
    }
    return true;
}

}  // namespace Wsts
