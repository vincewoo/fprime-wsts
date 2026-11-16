// ======================================================================
// \title  SimDirectory.hpp
// \brief  The Os::Directory of a simulated deployment: every path inside the sandbox
//
// Chosen by a simulated deployment in place of Os_File_Posix (CHOOSES_IMPLEMENTATIONS
// Wsts_Os_File, which carries the directory with it). The directory that flight software
// opens is mapped into the sandbox of the run (SimPath.hpp) first, so that a listing of
// what the target had under /ram0 is a listing of what the run has under its own
// directory.
// ======================================================================
#ifndef WSTS_OS_SIMDIRECTORY_HPP
#define WSTS_OS_SIMDIRECTORY_HPP

#include <Os/Posix/Directory.hpp>

namespace Wsts {

class SimDirectory : public Os::Posix::Directory::PosixDirectory {
  public:
    SimDirectory() = default;
    ~SimDirectory() override = default;

    //! \brief open or create a directory
    //!
    //! The path is mapped into the sandbox first. In a mode that creates, the
    //! directories in front of it are made if they are missing, like the mounts of the
    //! target were there; in CREATE_EXCLUSIVE the directory itself is left to the POSIX
    //! implementation, so that one already there is still reported.
    //!
    //! \param path: path of directory to open, as flight software names it
    //! \param mode: enum (READ, CREATE_IF_MISSING, CREATE_EXCLUSIVE)
    //! \return status of the operation
    //!
    Status open(const char* path, OpenMode mode) override;
};

}  // namespace Wsts

#endif
