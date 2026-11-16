// ======================================================================
// \title  SimPath.hpp
// \brief  The file sandbox of a simulated deployment: where flight paths land on the host
//
// The flight software of a simulated deployment opens files by target path (/ram0,
// /tffs0) or by a relative name. SimPath maps every such path into one sandbox directory
// on the host, so that what the target had as mounts is a directory tree, and nothing
// outside the sandbox is reachable. The Wsts implementations of Os::File, Os::FileSystem
// and Os::Directory (SimFile.hpp, SimFileSystem.hpp, SimDirectory.hpp) map through this
// class.
// ======================================================================
#ifndef WSTS_OS_SIMPATH_HPP
#define WSTS_OS_SIMPATH_HPP

#include <Fw/FPrimeBasicTypes.hpp>
#include <Os/FilePathUtils.hpp>
#include <string>

namespace Wsts {

class SimPath {
  public:
    //! \brief where flight files land on the host
    //!
    //! WSTS_FILES_DIR, else WSTS_DIR, else the working directory of the process, made
    //! absolute
    static std::string sandbox();

    //! \brief resolve a flight path to its canonical form, as flight software sees it
    //!
    //! A relative path is resolved against the working directory of flight software
    //! (workingDirectory()), which starts at the root of the sandbox. `.` and `..` are
    //! collapsed, without touching the file system.
    static Os::FilePathUtils::Status resolve(const char* path, std::string& canonical);

    //! \brief map a flight path into the sandbox
    //!
    //! The resolved path of `path` (see resolve()) with the sandbox in front of it. A
    //! path that would land outside the sandbox returns OUTSIDE_SANDBOX.
    static Os::FilePathUtils::Status map(const char* path, std::string& mapped);

    //! \brief the working directory of flight software, as flight software sees it
    //!
    //! The root of the sandbox ("/") to start: what Os::FileSystem::getWorkingDirectory
    //! reports, and what relative flight paths resolve against
    static std::string workingDirectory();

    //! \brief set the working directory of flight software
    //!
    //! The caller has resolved and mapped the path already: this records its canonical
    //! form
    static void setWorkingDirectory(const std::string& canonical);

    //! \brief create a directory of a mapped path and its missing parents (mkdir -p)
    //!
    //! The target had /ram0 and /tffs0 mounted; the sandbox makes what a write needs on
    //! demand. With `withLast` the directory the path names itself is made as well;
    //! without it only the parents, so that a creation mode of the caller still sees
    //! what is there.
    static bool makeDirectories(const std::string& mapped, bool withLast);
};

}  // namespace Wsts

#endif
